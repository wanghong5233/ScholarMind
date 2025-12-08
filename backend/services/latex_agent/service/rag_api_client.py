"""
RAG 服务 API 客户端
封装对主 RAG API 的所有调用，统一处理认证、错误、重试
"""
from typing import Dict, Any, List, Optional
import secrets
from datetime import timedelta
import logging

import httpx
from fastapi_jwt import JwtAccessBearerCookie

from config import settings

logger = logging.getLogger(__name__)


class RAGAPIClient:
    """
    RAG 服务 API 客户端
    
    职责：
    - 统一的服务间认证（JWT token 生成）
    - 统一的错误处理和日志
    - 统一的超时和重试策略
    - 封装所有对主 RAG API 的调用
    """
    
    def __init__(self):
        self.base_url = settings.RAG_SERVICE_URL
        self.timeout = 60.0  # 内部服务调用超时
        
        # 初始化 JWT helper（单例模式）
        self._jwt_helper: Optional[JwtAccessBearerCookie] = None
        if settings.JWT_SECRET_KEY:
            self._jwt_helper = JwtAccessBearerCookie(
                secret_key=settings.JWT_SECRET_KEY,
                auto_error=False,
                access_expires_delta=timedelta(days=settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS)
            )
    
    def _generate_service_token(self, user_id: int) -> Optional[str]:
        """生成服务间调用的 JWT token"""
        if not self._jwt_helper:
            logger.warning("JWT helper not initialized, service token unavailable")
            return None
        
        subject = {
            "user_id": user_id,
            "user_name": f"latex-agent-{user_id}",
            "salting": secrets.token_hex(8),
        }
        
        try:
            return self._jwt_helper.create_access_token(subject=subject)
        except Exception as exc:
            logger.error("Failed to create service JWT: %s", exc, exc_info=True)
            return None
    
    def _build_headers(self, user_id: int) -> Dict[str, str]:
        """构建请求头（包含认证信息）"""
        headers = {"X-User-Id": str(user_id)}
        
        service_token = self._generate_service_token(user_id)
        if service_token:
            headers["Authorization"] = f"Bearer {service_token}"
        
        return headers
    
    async def retrieve(
        self,
        query: str,
        kb_id: int,
        user_id: int,
        top_k: int = 5,
        focus_doc_ids: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        """
        调用内部检索 API
        
        Args:
            query: 查询文本
            kb_id: 知识库 ID
            user_id: 用户 ID（用于认证）
            top_k: 返回数量
            focus_doc_ids: 可选的聚焦文档 ID 列表
        
        Returns:
            检索结果列表
        
        Raises:
            httpx.HTTPStatusError: API 调用失败
            Exception: 其他错误
        """
        headers = self._build_headers(user_id)
        params = {
            "q": query,
            "kb_id": kb_id,
            "top_k": top_k
        }
        
        if focus_doc_ids:
            params["focus_doc_ids"] = ",".join(str(id) for id in focus_doc_ids)
        
        url = f"{self.base_url}/api/internal/retrieve"
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.debug(f"Calling RAG API: {url} with query={query[:50]}...")
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                results = response.json()
                
                logger.info(f"RAG API retrieve successful: kb_id={kb_id}, results={len(results)}")
                return results
        
        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAG API retrieve failed: {e.response.status_code} {e.response.text}",
                exc_info=True
            )
            raise
        
        except httpx.TimeoutException as e:
            logger.error(f"RAG API retrieve timeout: {e}", exc_info=True)
            raise
        
        except Exception as e:
            logger.error(f"RAG API retrieve error: {e}", exc_info=True)
            raise
    
    async def list_knowledge_bases(self, user_id: int) -> List[Dict[str, Any]]:
        """
        获取用户的知识库列表
        
        Args:
            user_id: 用户 ID
        
        Returns:
            知识库列表
        """
        headers = self._build_headers(user_id)
        url = f"{self.base_url}/api/knowledgebases/"
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.debug(f"Calling RAG API: {url}")
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
                
                if not isinstance(data, list):
                    logger.warning(f"Unexpected knowledgebases response format: {data}")
                    raise ValueError("知识库数据格式异常")
                
                logger.info(f"RAG API list_knowledge_bases successful: count={len(data)}")
                return data
        
        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAG API list_knowledge_bases failed: {e.response.status_code} {e.response.text}",
                exc_info=True
            )
            raise
        
        except Exception as e:
            logger.error(f"RAG API list_knowledge_bases error: {e}", exc_info=True)
            raise


# 单例模式：全局唯一的 API 客户端实例
_rag_api_client: Optional[RAGAPIClient] = None


def get_rag_api_client() -> RAGAPIClient:
    """获取 RAG API 客户端单例"""
    global _rag_api_client
    if _rag_api_client is None:
        _rag_api_client = RAGAPIClient()
    return _rag_api_client

