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

# 启动时检测GPU
gpu_available = check_gpu_available()

app = FastAPI(title="MinerU Parse Service", version="1.0.0")


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "ok", 
        "service": "mineru",
        "gpu_available": gpu_available
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
            
            json_files = []
            if os.path.exists(output_dir):
                for root, dirs, files in os.walk(output_dir):
                    for f in files:
                        if f.endswith('.json'):
                            json_files.append(os.path.join(root, f))
            
            if not json_files:
                logger.warning(f"No JSON output found in {output_dir}, using fallback")
                data = await _parse_with_python_api(input_path)
            else:
                # 读取第一个 JSON 文件
                with open(json_files[0], "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"Loaded JSON from {json_files[0]}")
            
            logger.info(f"Successfully parsed {file.filename}")
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

