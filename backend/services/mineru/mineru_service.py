"""
MinerU HTTP 包装服务
提供统一的 /parse 接口，接收 PDF 文件并返回结构化 JSON
"""
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import tempfile
import os
import json
import logging
import subprocess
from pathlib import Path

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mineru_service")

# 检测GPU可用性
def check_gpu_available():
    """检测是否有可用的GPU"""
    try:
        import torch
        if torch.cuda.is_available():
            gpu_count = torch.cuda.device_count()
            gpu_name = torch.cuda.get_device_name(0) if gpu_count > 0 else "Unknown"
            logger.info(f"GPU detected: {gpu_count} device(s), primary: {gpu_name}")
            return True
        else:
            logger.info("No GPU detected, will use CPU")
            return False
    except ImportError:
        logger.warning("PyTorch not found, cannot detect GPU")
        return False
    except Exception as e:
        logger.warning(f"GPU detection failed: {e}, will use CPU")
        return False

# 检查MinerU模型完整性
def check_mineru_models():
    """
    检查MinerU关键模型文件是否存在
    返回: (is_complete: bool, missing_count: int, total_count: int)
    """
    model_base = Path("/root/.cache/modelscope/hub/models/OpenDataLab/PDF-Extract-Kit-1.0/models")
    
    # 关键模型文件列表（基于实际预热日志）
    critical_models = [
        "MFD/YOLO/yolo_v8_ft.pt",
        "MFR/unimernet_hf_small_2503/model.safetensors",
        "Layout/YOLO/doclayout_yolo_docstructbench_imgsz1280_2501.pt",
        "OCR/paddleocr_torch/ch_PP-OCRv5_det_infer.pth",
        "OCR/paddleocr_torch/ch_PP-OCRv4_rec_server_doc_infer.pth",
        "OCR/paddleocr_torch/ch_PP-OCRv5_rec_infer.pth",
        "TabRec/UnetStructure/unet.onnx",
        "TabRec/SlanetPlus/slanet-plus.onnx",
        "TabCls/paddle_table_cls/PP-LCNet_x1_0_table_cls.onnx",
        "OriCls/paddle_orientation_classification/PP-LCNet_x1_0_doc_ori_cls.onnx",
        "ReadingOrder/layout_reader/model.safetensors",
    ]
    
    missing = []
    for model_path in critical_models:
        full_path = model_base / model_path
        if not full_path.exists():
            missing.append(model_path)
    
    total = len(critical_models)
    missing_count = len(missing)
    is_complete = missing_count == 0
    
    if is_complete:
        logger.info(f"[MODEL_CHECK] ✓ All {total} critical models present")
    else:
        logger.warning(f"[MODEL_CHECK] ✗ Missing {missing_count}/{total} models: {missing[:3]}...")
    
    return is_complete, missing_count, total

# 自动预热MinerU模型
def preheat_mineru_models():
    """
    使用示例PDF自动预热MinerU，触发模型下载
    """
    logger.info("[MODEL_PREHEAT] Starting automatic model download...")
    
    try:
        # 查找现有的测试PDF文件
        test_pdf_candidates = [
            "/tmp/preheat.pdf",
            "/app/test.pdf", 
            "/tmp/test.pdf"
        ]
        
        preheat_pdf = None
        for candidate in test_pdf_candidates:
            if os.path.exists(candidate):
                preheat_pdf = candidate
                logger.info(f"[MODEL_PREHEAT] Found existing test PDF: {preheat_pdf}")
                break
        
        # 如果没有现成的PDF，创建一个最小的PDF
        if not preheat_pdf:
            preheat_pdf = "/tmp/mineru_preheat_auto.pdf"
            logger.info(f"[MODEL_PREHEAT] Creating minimal test PDF: {preheat_pdf}")
            
            # 创建最小PDF（使用原始PDF格式，不依赖第三方库）
            pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 55 >>
stream
BT
/F1 12 Tf
100 700 Td
(MinerU Model Preheat Test) Tj
ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000262 00000 n 
0000000371 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
450
%%EOF
"""
            with open(preheat_pdf, "wb") as f:
                f.write(pdf_content)
            logger.info(f"[MODEL_PREHEAT] Created minimal PDF: {preheat_pdf}")
        
        # 执行MinerU预热命令
        cmd = f"mineru -p {preheat_pdf} -o /tmp/mineru_preheat_output"
        logger.info(f"[MODEL_PREHEAT] Executing: {cmd}")
        logger.info("[MODEL_PREHEAT] This may take 10-20 minutes for first-time model download...")
        
        result = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=1800,  # 30分钟超时，首次下载可能很慢
            text=True
        )
        
        if result.returncode == 0:
            logger.info("[MODEL_PREHEAT] ✓ Preheat completed successfully")
            # 清理临时文件
            try:
                if preheat_pdf == "/tmp/mineru_preheat_auto.pdf":
                    os.remove(preheat_pdf)
                import shutil
                shutil.rmtree("/tmp/mineru_preheat_output", ignore_errors=True)
            except:
                pass
            return True
        else:
            logger.error(f"[MODEL_PREHEAT] ✗ Preheat failed: {result.stderr[:500]}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("[MODEL_PREHEAT] ✗ Preheat timeout (>30min)")
        return False
    except Exception as e:
        logger.error(f"[MODEL_PREHEAT] ✗ Preheat error: {e}")
        return False

# 启动时检测GPU
gpu_available = check_gpu_available()

# 全局变量：模型就绪状态
models_ready = False

# 后台异步预热任务
async def async_preheat_models():
    """后台异步执行模型预热"""
    global models_ready
    
    import asyncio
    
    # 在后台线程中执行同步的预热函数
    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(None, preheat_mineru_models)
    
    if success:
        # 重新检查模型
        is_complete, _, _ = check_mineru_models()
        if is_complete:
            models_ready = True
            logger.info("[BACKGROUND_PREHEAT] ✓ Auto-preheat successful, MinerU now ready")
        else:
            logger.error("[BACKGROUND_PREHEAT] ✗ Models still incomplete after preheat")
    else:
        logger.error("[BACKGROUND_PREHEAT] ✗ Auto-preheat failed, manual intervention required")
        logger.error("[BACKGROUND_PREHEAT] Please run: docker compose exec mineru bash -c 'mineru -p /tmp/preheat.pdf -o /tmp/warmup'")

app = FastAPI(title="MinerU Parse Service", version="1.0.0")

@app.on_event("startup")
async def startup_event():
    """应用启动时检查模型并自动预热"""
    global models_ready
    
    logger.info("[STARTUP] Checking MinerU models...")
    is_complete, missing_count, total_count = check_mineru_models()
    
    if is_complete:
        models_ready = True
        logger.info("[STARTUP] ✓ MinerU ready to serve")
    else:
        logger.warning(f"[STARTUP] Models incomplete ({missing_count}/{total_count} missing)")
        logger.info("[STARTUP] Auto-preheat will run in background (may take 10-20 minutes)")
        logger.info("[STARTUP] Service will remain in 'degraded' state until models are ready")
        
        # 在后台异步执行预热，不阻塞服务启动
        import asyncio
        asyncio.create_task(async_preheat_models())


@app.get("/health")
async def health():
    """
    健康检查
    只有模型完全就绪时才返回healthy状态
    """
    if models_ready:
        return {
            "status": "healthy", 
            "service": "mineru",
            "gpu_available": gpu_available,
            "models_ready": True
        }
    else:
        # 模型未就绪，返回degraded状态（但HTTP 200，避免容器被kill）
        return {
            "status": "degraded",
            "service": "mineru",
            "gpu_available": gpu_available,
            "models_ready": False,
            "message": "Models still loading or incomplete"
        }


@app.post("/parse")
async def parse_pdf(file: UploadFile = File(...)):
    """
    解析 PDF 文件，返回结构化 JSON
    
    返回格式：
    {
        "pages": [
            {
                "page": 1,
                "elements": [
                    {
                        "type": "paragraph|table|equation|figure",
                        "text": "...",
                        "bbox": [x0, y0, x1, y1],
                        "confidence": 0.95,
                        "table": {...},  # 仅 table 类型
                        "latex": "...",  # 仅 equation 类型
                        "caption": "..." # 仅 figure 类型
                    }
                ]
            }
        ]
    }
    """
    # 检查模型是否就绪
    if not models_ready:
        raise HTTPException(
            status_code=503, 
            detail="MinerU models not ready. Service is still initializing or models are incomplete."
        )
    
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    # 保存上传文件到临时目录
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.pdf")
        output_path = os.path.join(tmpdir, "output.json")
        
        try:
            # 保存上传的文件
            with open(input_path, "wb") as f:
                content = await file.read()
                f.write(content)
            
            logger.info(f"Processing file: {file.filename}, size: {len(content)} bytes")
            
            # 调用 MinerU 的官方 CLI 命令：mineru
            import subprocess
            
            # MinerU 的官方命令是 mineru（不是 magic-pdf）
            # 参数：-p 输入文件，-o 输出目录
            cmd = f'mineru -p "{input_path}" -o "{tmpdir}"'
            
            logger.info(f"Executing: {cmd}")
            
            result = subprocess.run(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=1500,  # 25分钟超时，应对复杂PDF（留出缓冲）
                text=True
            )
            
            if result.returncode != 0:
                logger.error(f"MinerU failed: {result.stderr}")
                raise HTTPException(
                    status_code=500,
                    detail=f"MinerU parsing failed: {result.stderr[:200]}"
                )
            
            # mineru 输出到目录，查找生成的 JSON 文件
            # MinerU 会在 tmpdir 下创建以文件名命名的子目录
            # 输出结构：tmpdir/文件名/文件名.json 或 content_list.json
            output_dir = tmpdir
            
            # 详细日志：列出所有生成的文件
            logger.info(f"Checking output directory: {output_dir}")
            if os.path.exists(output_dir):
                all_files = []
                for root, dirs, files in os.walk(output_dir):
                    for f in files:
                        full_path = os.path.join(root, f)
                        all_files.append(full_path)
                        logger.info(f"Found file: {full_path} (size: {os.path.getsize(full_path)} bytes)")
                
                if not all_files:
                    logger.warning(f"Output directory {output_dir} is empty!")
            else:
                logger.error(f"Output directory {output_dir} does not exist!")
                # 列出 tmpdir 的内容
                logger.info(f"Contents of tmpdir ({tmpdir}):")
                for item in os.listdir(tmpdir):
                    item_path = os.path.join(tmpdir, item)
                    logger.info(f"  - {item} ({'dir' if os.path.isdir(item_path) else 'file'})")
            
            preferred_json = None
            fallback_json = None
            if os.path.exists(output_dir):
                for root, dirs, files in os.walk(output_dir):
                    for f in files:
                        if not f.endswith('.json'):
                            continue
                        full_path = os.path.join(root, f)
                        if f == "input_content_list.json":
                            preferred_json = full_path
                        elif f == "input_middle.json":
                            fallback_json = full_path
                        elif preferred_json is None and fallback_json is None:
                            fallback_json = full_path
            
            load_path = preferred_json or fallback_json
            if not load_path:
                logger.warning(f"No usable JSON output found in {output_dir}, using fallback parser")
                data = await _parse_with_python_api(input_path)
            else:
                with open(load_path, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                logger.info(f"Loaded JSON from {load_path}")
                
                # 标准化为扁平数组格式，确保mineru_parser.py能正确解析
                if isinstance(raw_data, list):
                    # 已经是数组，直接使用
                    data = raw_data
                    logger.info(f"[MINERU_FORMAT] Direct array, length={len(data)}")
                elif isinstance(raw_data, dict):
                    # 尝试提取内容数组
                    if raw_data.get("content_list"):
                        data = raw_data["content_list"]
                        logger.info(f"[MINERU_FORMAT] Extracted content_list, length={len(data)}")
                    elif raw_data.get("elements"):
                        data = raw_data["elements"]
                        logger.info(f"[MINERU_FORMAT] Extracted elements, length={len(data)}")
                    else:
                        # 检查是否是旧的input_middle.json格式（pdf_info -> pages）
                        pdf_info = raw_data.get("pdf_info", {})
                        if pdf_info and pdf_info.get("pages"):
                            # 展开pages为扁平数组
                            flat_elements = []
                            for page in pdf_info["pages"]:
                                page_idx = page.get("page_idx", 0)
                                for block in page.get("blocks", []):
                                    block["page_idx"] = page_idx
                                    flat_elements.append(block)
                            data = flat_elements
                            logger.info(f"[MINERU_FORMAT] Flattened from pdf_info.pages, length={len(data)}")
                        else:
                            logger.warning(f"[MINERU_FORMAT] Unknown dict structure, keys={list(raw_data.keys())[:20]}")
                            data = []
                else:
                    logger.error(f"[MINERU_FORMAT] Unexpected type: {type(raw_data)}")
                    data = []
                
                # 打印样本元素供调试
                if data and len(data) > 0:
                    sample = data[0]
                    if isinstance(sample, dict):
                        logger.info(f"[MINERU_SAMPLE] First element: type={sample.get('type')}, page_idx={sample.get('page_idx')}, has_text={bool(sample.get('text'))}")
                    else:
                        logger.warning(f"[MINERU_SAMPLE] First element is not dict: {type(sample)}")
                elif not data:
                    logger.error(f"[MINERU_EMPTY] No elements extracted from {os.path.basename(load_path)}")
            
            logger.info(f"Successfully parsed {file.filename}, returning {len(data) if isinstance(data, list) else 'unknown'} elements")
            return JSONResponse(content=data)
            
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="MinerU parsing timeout")
        except Exception as e:
            logger.exception(f"Error parsing {file.filename}")
            raise HTTPException(status_code=500, detail=f"Parsing error: {str(e)}")


async def _parse_with_python_api(pdf_path: str) -> dict:
    """
    使用 MinerU Python API 直接解析（备用方案）
    根据实际 MinerU API 调整
    """
    try:
        # 示例：假设 MinerU 提供 Python API
        # from mineru import PDFParser
        # parser = PDFParser()
        # result = parser.parse(pdf_path)
        # return result.to_dict()
        
        # 占位实现：返回基本结构
        logger.warning("Using fallback parser (MinerU Python API not configured)")
        return {
            "pages": [],
            "metadata": {
                "parser": "mineru_fallback",
                "note": "MinerU Python API not fully configured"
            }
        }
    except Exception as e:
        logger.error(f"Python API fallback failed: {e}")
        raise


if __name__ == "__main__":
    import uvicorn
    # 增加超时以支持大文件/复杂PDF（与 subprocess timeout 对齐）
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8001,
        timeout_keep_alive=1800,  # 30分钟 keep-alive
        timeout_graceful_shutdown=30
    )

