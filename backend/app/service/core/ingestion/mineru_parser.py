from __future__ import annotations

from typing import List, Tuple
import re
from service.core.ingestion.interfaces import DocumentParser, ParsedBlock
from utils.get_logger import log
from core.config import settings
import os
import json
import tempfile
import subprocess
from service.core.ingestion.ocr_engines import OCREngine
from service.core.ingestion.vision_engines import VisionEngine, get_vision_engine


class MinerUParser(DocumentParser):
    """MinerU 主解析器。

    优先级：HTTP → CLI → PyMuPDF 兜底。
    产出：ParsedBlock 列表，metadata 至少包含 parser_engine、element_type、page、bbox（可选）、confidence（可选）。
    """

    def name(self) -> str:
        return "MinerUParser"

    def parse(self, *, file_path: str) -> List[ParsedBlock]:
        mode = getattr(settings, "SM_MINERU_MODE", "auto")
        endpoint = getattr(settings, "SM_MINERU_ENDPOINT", None)
        
        log.info(f"MinerUParser.start file={file_path} mode={mode} endpoint={endpoint}")
        
        # 生产环境：必须配置 HTTP endpoint
        if not endpoint:
            raise RuntimeError(
                "MinerU HTTP endpoint 未配置！请设置环境变量 SM_MINERU_ENDPOINT=http://mineru:8001"
            )

        # 1) HTTP 模式（生产环境唯一方式）
        if mode in ("auto", "http"):
            try:
                blocks = self._call_http(file_path)
                if blocks and any((b.text or "").strip() for b in blocks):
                    log.info(f"MinerUParser.http.ok blocks={len(blocks)}")
                    return blocks
                log.error("MinerUParser.http.empty - MinerU 返回空结果")
                raise RuntimeError("MinerU HTTP 返回空结果")
            except Exception as e:
                log.error(f"MinerUParser.http.fail err={e}")
                raise RuntimeError(f"MinerU HTTP 调用失败: {e}")

        # 2) CLI 模式（仅开发环境，需要容器内安装 magic-pdf）
        if mode == "cli":
            try:
                blocks = self._call_cli(file_path)
                if blocks and any((b.text or "").strip() for b in blocks):
                    log.info(f"MinerUParser.cli.ok blocks={len(blocks)}")
                    return blocks
                log.error("MinerUParser.cli.empty")
                raise RuntimeError("MinerU CLI 返回空结果")
            except Exception as e:
                log.error(f"MinerUParser.cli.fail err={e}")
                raise RuntimeError(f"MinerU CLI 调用失败: {e}")

        raise RuntimeError(f"不支持的 MinerU 模式: {mode}")

    # ------------------------
    # HTTP 集成
    # ------------------------
    def _call_http(self, file_path: str) -> List[ParsedBlock]:
        import requests

        url = (settings.SM_MINERU_ENDPOINT or "").rstrip("/") + settings.SM_MINERU_HTTP_ROUTE
        files = {settings.SM_MINERU_HTTP_FILE_FIELD: open(file_path, "rb")}
        try:
            r = requests.post(url, files=files, timeout=settings.SM_MINERU_TIMEOUT_SECS)
        finally:
            try:
                files[settings.SM_MINERU_HTTP_FILE_FIELD].close()
            except Exception:
                pass
        r.raise_for_status()
        data = r.json()
        return self._parse_mineru_json(data)

    # ------------------------
    # CLI 集成
    # ------------------------
    def _call_cli(self, file_path: str) -> List[ParsedBlock]:
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "mineru_out.json")
            cmd = (settings.SM_MINERU_CLI_CMD or "").format(
                bin=settings.SM_MINERU_CLI_BIN,
                input=file_path,
                output=out_path,
            )
            cp = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=settings.SM_MINERU_TIMEOUT_SECS)
            if cp.returncode != 0:
                raise RuntimeError(f"mineru cli exit {cp.returncode}: {cp.stderr.decode('utf-8', errors='ignore')}")
            if not os.path.exists(out_path):
                return []
            with open(out_path, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
        return self._parse_mineru_json(data)

    # ------------------------
    # JSON 解析
    # ------------------------
    def _parse_mineru_json(self, data: dict | list) -> List[ParsedBlock]:
        """将 MinerU 的 JSON 转为 ParsedBlock 列表。
        
        MinerU 的 content_list.json 是一个扁平化数组，每个元素包含：
        - type: "text", "image", "table", "inline_equation", "interline_equation" 等
        - page_idx: 页码（从 0 开始）
        - text: 文本内容
        - bbox: [x0, y0, x1, y1] 边界框
        - latex: 公式的 LaTeX 代码（如果是公式）
        - img_path: 图片路径（如果是图片/表格）
        """
        blocks: List[ParsedBlock] = []
        
        # MinerU 返回的是扁平化数组
        elements = []
        if isinstance(data, list):
            elements = data
        elif isinstance(data, dict):
            # 兼容可能的包装结构
            elements = data.get("content_list") or data.get("elements") or []
        
        if not elements:
            log.warning("MinerUParser._parse_mineru_json: empty elements")
            return []
        
        log.info(f"MinerUParser._parse_mineru_json: processing {len(elements)} elements")
        
        # 调试：打印前3个元素的原始结构
        for i, el in enumerate(elements[:3]):
            log.info(f"[DEBUG_RAW_ELEMENT_{i+1}] type={el.get('type')} page={el.get('page_idx')} "
                     f"text_preview={str(el.get('text', ''))[:80]}... "
                     f"has_latex={bool(el.get('latex'))} has_table={bool(el.get('table'))}")
        
        # 按页分组以便统计
        pages_dict = {}
        for el in elements:
            page_idx = el.get("page_idx", 0)
            if page_idx not in pages_dict:
                pages_dict[page_idx] = []
            pages_dict[page_idx].append(el)
        
        log.info(f"MinerUParser._parse_mineru_json: grouped into {len(pages_dict)} pages")
        
        figs_for_vision: List[Tuple[dict, int]] = []  # (el, page_no)
        elem_stats = {"table": 0, "equation": 0, "figure": 0, "text": 0, "image": 0}
        
        # 遍历所有元素
        for el in elements:
            page_no = el.get("page_idx", 0) + 1  # MinerU 的 page_idx 从 0 开始
            etype = str(el.get("type", "text")).lower()
            
            md = {
                "parser_engine": "mineru",
                "page": page_no,
            }
            
            bbox = el.get("bbox") or el.get("box")
            if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                md["bbox"] = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
            
            conf = el.get("confidence")
            if isinstance(conf, (int, float)):
                md["confidence"] = float(conf)
            
            # 归一 element_type 并提取文本
            text = ""
            if etype in ("table", "table_json"):
                md["element_type"] = "table_json"
                text = el.get("markdown") or el.get("text") or json.dumps(el.get("table") or {}, ensure_ascii=False)
                md["table_json"] = el.get("table") or el.get("structure") or {}
                elem_stats["table"] += 1
            elif etype in ("equation", "formula", "latex", "inline_equation", "interline_equation"):
                md["element_type"] = "equation_latex"
                text = el.get("latex") or el.get("text") or ""
                md["equation_latex"] = text
                elem_stats["equation"] += 1
            elif etype in ("figure", "image", "chart"):
                md["element_type"] = "figure_summary"
                text = el.get("caption") or el.get("text") or ""
                elem_stats["image"] += 1
                # 暂存待 Vision 引擎生成摘要
                figs_for_vision.append((el, page_no))
            else:
                # text, paragraph, title 等
                md["element_type"] = "paragraph"
                text = el.get("text") or ""
                elem_stats["text"] += 1
            
            # 添加 block
            text = (text or "").strip()
            if text:
                blocks.append(ParsedBlock(text=text, metadata=md))
        
        # 统一处理图表语义生成（按页限额）
        self._augment_figures_with_vision(blocks, figs_for_vision)

        # 为公式块生成自然语言描述以改善嵌入质量
        self._augment_equation_description(blocks)
        
        # 调试：打印前2个解析后的 block（预合并前）
        for i, block in enumerate(blocks[:2]):
            log.info(f"[DEBUG_PARSED_BLOCK_{i+1}] element_type={block.metadata.get('element_type')} "
                     f"page={block.metadata.get('page')} len={len(block.text)} "
                     f"text_preview={block.text[:100]}...")
        
        # 预合并：将相邻的同类型、同页的 text blocks 合并，减少碎片化
        merged_blocks = self._merge_adjacent_text_blocks(blocks)
        
        # 调试：打印前2个预合并后的 block
        for i, block in enumerate(merged_blocks[:2]):
            log.info(f"[DEBUG_MERGED_BLOCK_{i+1}] element_type={block.metadata.get('element_type')} "
                     f"page={block.metadata.get('page')} len={len(block.text)} "
                     f"text_preview={block.text[:100]}...")
        
        # 调试：打印1个公式和1个表格样例（如果有）
        for block in merged_blocks:
            if block.metadata.get('element_type') == 'equation_latex' and 'equation_latex' in block.metadata:
                log.info(f"[DEBUG_EQUATION_SAMPLE] latex={block.metadata['equation_latex'][:150]}...")
                break
        for block in merged_blocks:
            if block.metadata.get('element_type') == 'table_json' and 'table_json' in block.metadata:
                log.info(f"[DEBUG_TABLE_SAMPLE] markdown_preview={block.text[:150]}...")
                break
        
        log.info(f"MinerUParser._parse_mineru_json: total_blocks={len(blocks)} merged_blocks={len(merged_blocks)} stats={elem_stats}")
        return merged_blocks

    def _merge_adjacent_text_blocks(self, blocks: List[ParsedBlock]) -> List[ParsedBlock]:
        """合并相邻的同类型、同页的文本块，减少碎片化。
        
        策略（针对 MinerU 句子级输出优化）：
        1. 按页分组
        2. 列感知：每页按 X0（bbox[0]）做列划分（1/2 列），每列内再按 Y 排序（阅读顺序）
        3. 合并相邻的 paragraph 块，直到达到目标大小（1000-2000 字符）
        4. 页级软上限：若该页文本块数 > 6，则放宽合并上限（至 2400-2800）继续合并，直至回落到 6 或无法继续
        5. 保留表格和公式块不合并
        """
        if not blocks:
            return blocks
        
        # 按页分组
        pages_blocks = {}
        for block in blocks:
            page = block.metadata.get("page", 1)
            if page not in pages_blocks:
                pages_blocks[page] = []
            pages_blocks[page].append(block)
        
        merged = []
        
        # 逐页处理
        for page in sorted(pages_blocks.keys()):
            page_blocks = pages_blocks[page]
            
            # 列感知：将该页拆分为 1/2 列
            columns = self._split_into_columns(page_blocks)
            page_text_before = sum(1 for b in page_blocks if b.metadata.get("element_type") == "paragraph")
            page_merged: List[ParsedBlock] = []
            
            # 按列合并
            for col_blocks in columns:
                # 按 Y 坐标排序（如果有 bbox）
                def get_y_coord(b):
                    bbox = b.metadata.get("bbox")
                    if bbox and len(bbox) >= 4:
                        return bbox[1]  # Y0 坐标
                    return 0
                col_blocks.sort(key=get_y_coord)
                
                current = None
                for block in col_blocks:
                    # 非文本块直接添加
                    if block.metadata.get("element_type") != "paragraph":
                        if current:
                            page_merged.append(current)
                            current = None
                        page_merged.append(block)
                        continue
                    
                    # 第一个文本块
                    if current is None:
                        current = block
                        continue
                    
                    # 检查是否可以合并（阈值来自配置）
                    # 目标范围：3000-10000 字符/块（约 750-2500 tokens），可通过设置微调
                    combined_len = len(current.text) + len(block.text) + 1
                    can_merge = combined_len <= getattr(settings, "SM_PREMERGE_MAX_CHARS", 10000)

                    # 首页更严格：避免把标题/作者/摘要/Index Terms/引言合成超长段
                    page_no = current.metadata.get("page", 1)
                    first_page_cap = getattr(settings, "SM_PREMERGE_MAX_CHARS_FIRST_PAGE", 2400)
                    if page_no == 1 and combined_len > first_page_cap:
                        can_merge = False

                    # 章节/标记边界：遇到 Abstract / Index Terms / Keywords / (I.|1.) Introduction 等不跨段合并
                    def _starts_section_heading(txt: str) -> bool:
                        return bool(re.match(r"\s*(?:Abstract(?:[—:])?|Index\s+Terms?|Keywords?|(?:I|1)\.\s*Introduction)\b", txt, re.IGNORECASE))

                    def _contains_tail_section_marker(txt: str) -> bool:
                        # 在末尾 80 字符内出现结束信号，如 "Index Terms" 或句号后紧跟章节编号
                        tail = (txt or "")[-120:]
                        if re.search(r"\bIndex\s+Terms?\b|\bKeywords?\b", tail, re.IGNORECASE):
                            return True
                        if re.search(r"\.\s+(?:[IVX]{1,4}|[0-9]{1,2})\.\s+[A-Z]", tail):
                            return True
                        return False

                    if _contains_tail_section_marker(current.text) or _starts_section_heading(block.text):
                        can_merge = False
                    
                    if can_merge:
                        current = ParsedBlock(
                            text=current.text + " " + block.text,
                            metadata=current.metadata.copy()
                        )
                    else:
                        page_merged.append(current)
                        current = block
                
                if current:
                    page_merged.append(current)
            
            # 页级软上限：若文本块 > 6，则强制合并最小相邻段落对至 ≤6（保持列内顺序与多模态断点）
            page_merged = self._soft_cap_page_blocks(page_merged, page)
            page_text_after = sum(1 for b in page_merged if b.metadata.get("element_type") == "paragraph")
            
            # 合并入总结果
            merged.extend(page_merged)
            
            # 日志
            log.info(
                f"MinerUParser.page_merge page={page} cols={len(columns)} text_before={page_text_before} text_after={page_text_after} total_after={len(page_merged)}"
            )
        
        return merged

    # 备用：外部也可调用的边界检测（目前仅内部使用）
    def _has_section_boundary(self, text: str) -> bool:
        if not text:
            return False
        if re.search(r"\b(?:Abstract|Index\s+Terms?|Keywords?)\b", text, re.IGNORECASE):
            return True
        if re.search(r"(?:^|\n)\s*(?:I|1)\.\s*Introduction\b", text, re.IGNORECASE):
            return True
        if re.search(r"\.\s+(?:[IVX]{1,4}|[0-9]{1,2})\.\s+[A-Z]", text):
            return True
        return False
    
    def _split_into_columns(self, page_blocks: List[ParsedBlock]) -> List[List[ParsedBlock]]:
        """根据段落块的 X0（bbox[0]）粗略拆分列（1/2列）。
        规则：
        - 若无 bbox 或文本块不足，返回单列
        - 寻找 X0 最大间隔作为列分割点，间隔 > 80 且两侧各 ≥ 3 个段落时判定为双列
        - 非 paragraph 块按其 X0 归入最近列
        """
        # 收集有 bbox 的 paragraph 的 x0
        para_with_bbox = []
        for b in page_blocks:
            if b.metadata.get("element_type") == "paragraph":
                bbox = b.metadata.get("bbox")
                if bbox and len(bbox) >= 4:
                    para_with_bbox.append((bbox[0], b))
        if len(para_with_bbox) < 6:
            return [page_blocks]
        
        # 按 x0 排序并寻找最大间隔
        para_with_bbox.sort(key=lambda t: t[0])
        x0s = [t[0] for t in para_with_bbox]
        gaps = []
        for i in range(1, len(x0s)):
            gaps.append((x0s[i] - x0s[i - 1], i))
        if not gaps:
            return [page_blocks]
        largest_gap, idx = max(gaps, key=lambda g: g[0])
        if largest_gap < 80:  # 粗略阈值，避免误判
            return [page_blocks]
        
        # 用该间隔作为分割点
        threshold = (x0s[idx - 1] + x0s[idx]) / 2.0
        left, right = [], []
        
        def assign_side(block: ParsedBlock):
            bbox = block.metadata.get("bbox")
            x0 = bbox[0] if bbox and len(bbox) >= 4 else None
            if x0 is None:
                # 没有 bbox 的，按文本块平均分配到左列
                return left
            return left if x0 <= threshold else right
        
        for b in page_blocks:
            side = assign_side(b)
            side.append(b)
        
        # 确保两侧段落数量足够，否则退化为单列
        left_para = sum(1 for b in left if b.metadata.get("element_type") == "paragraph")
        right_para = sum(1 for b in right if b.metadata.get("element_type") == "paragraph")
        if left_para < 3 or right_para < 3:
            return [page_blocks]
        
        return [left, right]
    
    def _soft_cap_page_blocks(self, page_blocks: List[ParsedBlock], page: int) -> List[ParsedBlock]:
        """页级软上限：若该页 paragraph 数量 > 6，则强制合并最小的相邻段落对。
        
        策略：不再放宽上限重新遍历（无效），而是每次找到最小的相邻段落对并强制合并，
        重复直到段落数 ≤ 6。这样可以确保一定能降到目标数量。
        """
        MAX_PARAS = 6
        paras = [b for b in page_blocks if b.metadata.get("element_type") == "paragraph"]
        if len(paras) <= MAX_PARAS:
            return page_blocks
        
        # 强制合并策略：每次找到最小的相邻段落对并合并，重复直到 ≤6 个
        iteration = 0
        while True:
            # 重新收集段落块（因为合并后顺序可能变化）
            para_blocks = [b for b in page_blocks if b.metadata.get("element_type") == "paragraph"]
            if len(para_blocks) <= MAX_PARAS:
                log.info(f"MinerUParser.page_soft_cap page={page} final_paras={len(para_blocks)} iterations={iteration}")
                return page_blocks
            
            # 找到最小的相邻段落对
            min_combined_len = float('inf')
            merge_idx = -1
            for i in range(len(page_blocks) - 1):
                if (page_blocks[i].metadata.get("element_type") == "paragraph" and
                    page_blocks[i + 1].metadata.get("element_type") == "paragraph"):
                    combined = len(page_blocks[i].text) + len(page_blocks[i + 1].text)
                    if combined < min_combined_len:
                        min_combined_len = combined
                        merge_idx = i
            
            if merge_idx == -1:
                # 没有相邻段落可合并（被表格/公式隔开）
                log.info(f"MinerUParser.page_soft_cap_noop page={page} paras={len(para_blocks)} (no adjacent pairs)")
                return page_blocks
            
            # 合并该对
            merged_block = ParsedBlock(
                text=page_blocks[merge_idx].text + " " + page_blocks[merge_idx + 1].text,
                metadata=page_blocks[merge_idx].metadata.copy()
            )
            page_blocks = page_blocks[:merge_idx] + [merged_block] + page_blocks[merge_idx + 2:]
            iteration += 1
            
            # 防止无限循环
            if iteration > 20:
                log.warning(f"MinerUParser.page_soft_cap_timeout page={page} paras={len(para_blocks)} iterations={iteration}")
                return page_blocks
        
        return page_blocks
    
    def _crop_image(self, el: dict) -> bytes | None:
        """根据 MinerU 元素的 bbox 裁剪原图（如 MinerU 提供原图/路径则可接入）。
        当前占位：若 el 提供 'image_png' 字段（base64），则直接返回。
        """
        try:
            import base64
            b64 = el.get("image_png")
            if isinstance(b64, str) and b64:
                return base64.b64decode(b64)
        except Exception:
            return None
        return None

    def _augment_figures_with_vision(self, blocks: List[ParsedBlock], figs: List[Tuple[dict, int]]) -> None:
        if not figs:
            return
        try:
            limit_per_2pages = max(int(getattr(settings, "SM_VISION_MAX_PER_2PAGES", 1)), 0)
        except Exception:
            limit_per_2pages = 1
        engine = get_vision_engine()
        if not engine.is_available():
            log.info(f"MinerUParser.VISION_DISABLED: {len(figs)} figures skipped")
            return
        
        log.info(f"MinerUParser.VISION_START: processing {len(figs)} figures, limit={limit_per_2pages}/2pages")
        # 简单的分页限额策略
        page_to_cnt: dict[int, int] = {}
        success_cnt = 0
        for el, page_no in figs:
            cnt = page_to_cnt.get((page_no - 1) // 2, 0)
            if cnt >= limit_per_2pages:
                continue
            crop = self._crop_image(el)
            if not crop:
                continue
            summary = engine.summarize_figure(crop, caption=el.get("caption") or "")
            if isinstance(summary, str) and summary.strip():
                blocks.append(ParsedBlock(text=summary.strip(), metadata={
                    "parser_engine": "mineru",
                    "element_type": "figure_summary",
                    "page": page_no,
                }))
                page_to_cnt[(page_no - 1) // 2] = cnt + 1
                success_cnt += 1
        
        if success_cnt:
            log.info(f"MinerUParser.VISION_OK: generated {success_cnt} figure summaries")

    def _augment_equation_description(self, blocks: List[ParsedBlock]) -> None:
        """Use LLM to generate natural language descriptions for equation blocks.

        Prepends a one-sentence description to the block text so the embedding
        captures semantic meaning in addition to raw LaTeX.
        Original LaTeX is preserved in metadata["equation_latex"].
        """
        if not getattr(settings, "SM_EQUATION_DESCRIPTION_ENABLED", False):
            return

        eq_blocks = [b for b in blocks if (b.metadata or {}).get("element_type") == "equation_latex"]
        if not eq_blocks:
            return

        try:
            from service.core.rag.llm.client import LLMClient
            llm = LLMClient(task="aux")
        except Exception as exc:
            log.warning("Failed to init LLM for equation description: %s", exc)
            return

        batch_latex = []
        for b in eq_blocks:
            latex = (b.metadata or {}).get("equation_latex") or b.text or ""
            batch_latex.append(latex.strip()[:500])

        prompt = (
            "你是学术论文公式解读助手。对以下每个 LaTeX 公式，给出一句话自然语言描述（中文），"
            "说明公式的含义或作用。每行对应一个公式，直接输出描述，不要编号。\n\n"
            + "\n".join(f"公式{i+1}: {ltx}" for i, ltx in enumerate(batch_latex))
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            result = llm.generate(
                messages,
                temperature=0.2,
                max_tokens=min(len(eq_blocks) * 80, 2048),
                stream=False,
            )
            if not isinstance(result, str):
                result = "".join(result)
        except Exception as exc:
            log.warning("Equation description LLM call failed: %s", exc)
            return

        lines = [ln.strip() for ln in result.strip().splitlines() if ln.strip()]

        augmented = 0
        for i, b in enumerate(eq_blocks):
            desc = lines[i] if i < len(lines) else None
            if desc:
                original_latex = b.text
                b.text = f"{desc}\n{original_latex}"
                augmented += 1

        if augmented:
            log.info("Equation description: augmented %d/%d equations", augmented, len(eq_blocks))

    # ------------------------
    # 兜底路径
    # ------------------------
    def _fallback_pymupdf(self, file_path: str) -> List[ParsedBlock]:
        blocks: List[ParsedBlock] = []
        try:
            import fitz
            with fitz.open(file_path) as doc:
                max_pages = min(getattr(settings, "SM_MINERU_MAX_PAGES", 30), len(doc))
                for i in range(max_pages):
                    try:
                        text = doc[i].get_text("text") or ""
                    except Exception:
                        text = ""
                    if text.strip():
                        blocks.append(ParsedBlock(text=text, metadata={
                            "page": i + 1,
                            "parser_engine": "mineru",
                            "element_type": "paragraph",
                        }))
        except Exception as e:
            log.warning(f"MinerUParser.fallback_pymupdf.fail err={e}")
            return [ParsedBlock(text="", metadata={"parser_engine": "mineru", "note": "pymupdf_failed"})]

        # 轻量多模态兜底（caption/table 正则）
        try:
            import re as _re
            fig_cnt = 0
            tbl_cnt = 0
            joined = "\n".join([(b.text or "") for b in blocks[:10]])
            for ln in [ln.strip() for ln in joined.splitlines() if ln.strip()]:
                low = ln.lower()
                if _re.match(r"^(figure|fig\.|图)\s*[\d:：]", low):
                    blocks.append(ParsedBlock(text=ln, metadata={
                        "element_type": "figure_summary",
                        "parser_engine": "mineru",
                    }))
                    fig_cnt += 1
                elif _re.match(r"^(table|表)\s*[\d:：]", low):
                    blocks.append(ParsedBlock(text=ln, metadata={
                        "element_type": "table_struct",
                        "parser_engine": "mineru",
                    }))
                    tbl_cnt += 1
            if fig_cnt or tbl_cnt:
                log.info(f"MinerUParser.fallback captions figures={fig_cnt} tables={tbl_cnt}")
        except Exception:
            pass
        try:
            log.info(f"MinerUParser.finish blocks={len(blocks)} (fallback)")
        except Exception:
            pass
        return blocks


