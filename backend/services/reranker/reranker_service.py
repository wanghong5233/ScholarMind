"""
BGE Reranker HTTP 服务
提供统一的 /rerank 接口，接收查询和候选块列表，返回重排序结果
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import logging
import torch

# 配置日志（确保输出到标准输出，以便Docker日志捕获）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler()  # 确保输出到标准输出
    ]
)
logger = logging.getLogger("reranker_service")

# 检测GPU可用性
def check_gpu_available():
    """检测是否有可用的GPU"""
    try:
        if torch.cuda.is_available():
            gpu_count = torch.cuda.device_count()
            gpu_name = torch.cuda.get_device_name(0) if gpu_count > 0 else "Unknown"
            logger.info(f"GPU detected: {gpu_count} device(s), primary: {gpu_name}")
            return True, gpu_count, gpu_name
        else:
            logger.info("No GPU detected, will use CPU")
            return False, 0, None
    except ImportError:
        logger.warning("PyTorch not found, cannot detect GPU")
        return False, 0, None
    except Exception as e:
        logger.warning(f"GPU detection failed: {e}, will use CPU")
        return False, 0, None

# 启动时检测GPU
gpu_available, gpu_count, gpu_name = check_gpu_available()

app = FastAPI(title="BGE Reranker Service", version="1.0.0")

# 全局模型实例
_reranker_model = None
_model_device = None


class RerankRequest(BaseModel):
    """精排请求"""
    query: str  # 查询文本
    chunks: List[Dict[str, Any]]  # 候选块列表，每个块包含 content, chunk_id, metadata 等
    batch_size: Optional[int] = 32  # 批处理大小


class RerankResponse(BaseModel):
    """精排响应"""
    reranked_chunks: List[Dict[str, Any]]  # 重排序后的块列表（按相关性分数降序）
    scores: List[float]  # 对应的相关性分数


def load_reranker_model():
    """加载精排模型（延迟加载）"""
    global _reranker_model, _model_device
    
    if _reranker_model is not None:
        return _reranker_model, _model_device
    
    import os
    from sentence_transformers import CrossEncoder
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, BitsAndBytesConfig
    
    model_path = os.getenv("RERANKER_MODEL_PATH", "/models/bge-reranker-large")
    device = "cuda" if gpu_available else "cpu"
    use_quantization = os.getenv("RERANKER_USE_QUANTIZATION", "true").lower() == "true"
    
    # 检查模型是否存在，如果不存在则自动下载
    if not os.path.exists(model_path) or not os.path.exists(os.path.join(model_path, "config.json")):
        logger.warning(f"Model not found at {model_path}, downloading automatically...")
        try:
            from huggingface_hub import snapshot_download
            os.makedirs(model_path, exist_ok=True)
            logger.info(f"Downloading BGE-Reranker-Large from HuggingFace...")
            snapshot_download(
                repo_id="BAAI/bge-reranker-large",
                local_dir=model_path,
                local_dir_use_symlinks=False
            )
            logger.info(f"Model download completed successfully!")
        except Exception as download_error:
            logger.error(f"Failed to download model: {download_error}", exc_info=True)
            raise RuntimeError(f"Model not found and download failed: {download_error}")
    
    logger.info(f"[MODEL_LOAD] Loading reranker model from: {model_path}")
    logger.info(f"[MODEL_LOAD] Device: {device}, Quantization: {use_quantization}")
    
    try:
        # 如果启用量化且设备是CUDA，使用bitsandbytes进行INT8量化
        if use_quantization and device == "cuda" and torch.cuda.is_available():
            try:
                quantization_config = BitsAndBytesConfig(
                    load_in_8bit=True,
                    llm_int8_threshold=6.0,
                    llm_int8_has_fp16_weight=False,
                )
                
                tokenizer = AutoTokenizer.from_pretrained(model_path)
                model = AutoModelForSequenceClassification.from_pretrained(
                    model_path,
                    quantization_config=quantization_config,
                    device_map="auto",
                    torch_dtype=torch.float16,
                )
                
                # 包装为CrossEncoder兼容接口
                _reranker_model = QuantizedCrossEncoderWrapper(model, tokenizer)
                logger.info(f"Reranker loaded with INT8 quantization on {device}. Estimated VRAM: ~5-6GB")
                
            except ImportError:
                logger.warning("bitsandbytes not available, falling back to standard loading")
                _reranker_model = CrossEncoder(model_path, device=device)
                logger.info(f"Reranker initialized (standard) on device: {device}")
            except Exception as quant_error:
                logger.warning(f"Quantization failed: {quant_error}, falling back to standard loading")
                _reranker_model = CrossEncoder(model_path, device=device)
                logger.info(f"Reranker initialized (fallback) on device: {device}")
        else:
            # 标准加载（无量化）
            _reranker_model = CrossEncoder(model_path, device=device)
            logger.info(f"Reranker initialized on device: {device}")
        
        _model_device = device
        return _reranker_model, _model_device
        
    except Exception as e:
        logger.error(f"Failed to load reranker model: {e}", exc_info=True)
        raise


class QuantizedCrossEncoderWrapper:
    """量化CrossEncoder包装器"""
    def __init__(self, model, tokenizer):
        self.model = model
        self.tokenizer = tokenizer
        self.model.eval()
    
    def predict(self, sentence_pairs, batch_size=32):
        """对句子对列表进行批量预测，返回相关性分数"""
        import numpy as np
        
        scores = []
        with torch.no_grad():
            for i in range(0, len(sentence_pairs), batch_size):
                batch = sentence_pairs[i:i + batch_size]
                
                # 对每个句子对进行tokenization
                inputs = self.tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                ).to(self.model.device)
                
                # 前向传播获取logits
                outputs = self.model(**inputs)
                logits = outputs.logits
                
                # 提取相关性分数（CrossEncoder输出是单个分数）
                batch_scores = logits.squeeze(-1).cpu().numpy()
                scores.extend(batch_scores)
        
        return np.array(scores)


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "service": "reranker",
        "gpu_available": gpu_available,
        "gpu_count": gpu_count,
        "gpu_name": gpu_name,
        "model_loaded": _reranker_model is not None,
        "device": _model_device if _model_device else "unknown"
    }


@app.post("/rerank", response_model=RerankResponse)
async def rerank(request: RerankRequest):
    """
    对候选块列表进行重排序
    
    请求体：
    {
        "query": "用户查询",
        "chunks": [
            {
                "chunk_id": "chunk_1",
                "content": "文本内容",
                "metadata": {...}
            },
            ...
        ],
        "batch_size": 32  # 可选
    }
    
    响应：
    {
        "reranked_chunks": [...],  # 按相关性降序排列
        "scores": [0.95, 0.87, ...]  # 对应的分数
    }
    """
    if not request.chunks:
        return RerankResponse(reranked_chunks=[], scores=[])
    
    try:
        # 延迟加载模型
        model, device = load_reranker_model()
        
        # 创建句子对：[(query, chunk_content), ...]
        sentence_pairs = [(request.query, chunk.get("content", "")) for chunk in request.chunks]
        
        logger.info(f"[RERANK_REQUEST] query='{request.query[:60]}...' chunks={len(request.chunks)} batch_size={request.batch_size}")
        
        # 使用模型进行预测
        scores = model.predict(sentence_pairs, batch_size=request.batch_size)
        
        # 将分数和原始块打包，然后按分数降序排序
        scored_chunks = list(zip(scores, request.chunks))
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        
        # 提取排序后的块和分数
        reranked_chunks = [chunk for score, chunk in scored_chunks]
        score_list = [float(score) for score, _ in scored_chunks]
        
        logger.info(f"[RERANK_COMPLETE] Top score: {score_list[0] if score_list else 0:.4f}, Score range: [{min(score_list) if score_list else 0:.4f}, {max(score_list) if score_list else 0:.4f}]")
        
        return RerankResponse(
            reranked_chunks=reranked_chunks,
            scores=score_list
        )
        
    except Exception as e:
        logger.error(f"Reranking failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Reranking failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)

