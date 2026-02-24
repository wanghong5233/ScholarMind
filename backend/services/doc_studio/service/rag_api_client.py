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

from core.config import settings

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
            "user_name": f"doc-studio-{user_id}",
            "service_name": "doc_studio",
            "token_use": "internal_service",
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

    async def get_history(
        self,
        session_id: str,
        user_id: int,
        question: str = "",
    ) -> Dict[str, Any]:
        """
        获取会话的 STM 历史切片（用于内部服务上下文注入）
        """
        headers = self._build_headers(user_id)
        params = {"question": question} if question else {}
        url = f"{self.base_url}/api/internal/history/{session_id}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.debug(f"Calling RAG API: {url} for session history")
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAG API history failed: {e.response.status_code} {e.response.text}",
                exc_info=True,
            )
            raise

        except httpx.TimeoutException as e:
            logger.error(f"RAG API history timeout: {e}", exc_info=True)
            raise

        except Exception as e:
            logger.error(f"RAG API history error: {e}", exc_info=True)
            raise

    async def get_profile(
        self,
        user_id: int,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        获取用户 LTM 记忆画像（用于内部服务上下文注入）
        """
        headers = self._build_headers(user_id)
        params = {"limit": limit}
        url = f"{self.base_url}/api/internal/profile/{user_id}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.debug(f"Calling RAG API: {url} for memory profile")
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAG API profile failed: {e.response.status_code} {e.response.text}",
                exc_info=True,
            )
            raise

        except httpx.TimeoutException as e:
            logger.error(f"RAG API profile timeout: {e}", exc_info=True)
            raise

        except Exception as e:
            logger.error(f"RAG API profile error: {e}", exc_info=True)
            raise

    async def get_context(
        self,
        session_id: str,
        user_id: int,
        question: str = "",
        memory_limit: int = 10,
    ) -> Dict[str, Any]:
        """
        获取统一的对话上下文包（STM + LTM + 格式化文本）
        """
        headers = self._build_headers(user_id)
        params = {"question": question, "memory_limit": memory_limit}
        url = f"{self.base_url}/api/internal/context/{session_id}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.debug(f"Calling RAG API: {url} for context pack")
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAG API context failed: {e.response.status_code} {e.response.text}",
                exc_info=True,
            )
            raise

        except httpx.TimeoutException as e:
            logger.error(f"RAG API context timeout: {e}", exc_info=True)
            raise

        except Exception as e:
            logger.error(f"RAG API context error: {e}", exc_info=True)
            raise

    async def get_session_detail(
        self,
        session_id: str,
        user_id: int,
    ) -> Dict[str, Any]:
        """获取会话详情（用于校验 Doc Studio 会话 surface）。"""
        headers = self._build_headers(user_id)
        url = f"{self.base_url}/api/sessions/{session_id}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.debug("Calling RAG API: %s for session detail", url)
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    logger.warning("Unexpected session detail response format: %s", data)
                    raise ValueError("会话详情数据格式异常")
                return data

        except httpx.HTTPStatusError as e:
            logger.error(
                "RAG API get_session_detail failed: %s %s",
                e.response.status_code,
                e.response.text,
                exc_info=True,
            )
            raise

        except httpx.TimeoutException as e:
            logger.error("RAG API get_session_detail timeout: %s", e, exc_info=True)
            raise

        except Exception as e:
            logger.error("RAG API get_session_detail error: %s", e, exc_info=True)
            raise

    async def append_message(
        self,
        session_id: str,
        user_id: int,
        user_question: str,
        model_answer: str,
        retrieval_content: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        追加一条会话消息（内部服务专用）
        """
        headers = self._build_headers(user_id)
        payload: Dict[str, Any] = {
            "session_id": session_id,
            "user_question": user_question,
            "model_answer": model_answer,
        }
        if retrieval_content is not None:
            payload["retrieval_content"] = retrieval_content
        if source:
            payload["source"] = source
        if trace_id:
            payload["trace_id"] = trace_id
        url = f"{self.base_url}/api/internal/messages"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.debug("Calling RAG API: %s to append message", url)
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAG API append_message failed: {e.response.status_code} {e.response.text}",
                exc_info=True,
            )
            raise

        except httpx.TimeoutException as e:
            logger.error(f"RAG API append_message timeout: {e}", exc_info=True)
            raise

        except Exception as e:
            logger.error(f"RAG API append_message error: {e}", exc_info=True)
            raise

    async def list_messages(
        self,
        session_id: str,
        user_id: int,
        page: int = 1,
        page_size: int = 200,
    ) -> Dict[str, Any]:
        """内部服务获取会话消息列表（用于 Doc Studio 加载对话历史）"""
        headers = self._build_headers(user_id)
        url = f"{self.base_url}/api/internal/sessions/{session_id}/messages"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    url,
                    headers=headers,
                    params={"page": page, "page_size": page_size},
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                "RAG API list_messages failed: %s %s",
                e.response.status_code,
                e.response.text,
                exc_info=True,
            )
            raise
        except Exception as e:
            logger.error("RAG API list_messages error: %s", e, exc_info=True)
            raise

    async def rewind_messages(
        self,
        session_id: str,
        user_id: int,
        keep_messages: Optional[int] = None,
        before_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Rewind a session to the first N messages (internal service call)."""
        headers = self._build_headers(user_id)
        url = f"{self.base_url}/api/internal/sessions/{session_id}/rewind"
        payload: Dict[str, Any] = {}
        if before_message_id:
            payload["before_message_id"] = str(before_message_id)
        elif keep_messages is not None:
            payload["keep_messages"] = max(int(keep_messages or 0), 0)
        else:
            payload["keep_messages"] = 0
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.info(
                    "Calling RAG API rewind_messages: session_id=%s keep_messages=%s before_message_id=%s",
                    session_id,
                    payload.get("keep_messages"),
                    payload.get("before_message_id"),
                )
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()
                logger.info("RAG API rewind_messages success: session_id=%s result=%s", session_id, result)
                return result
        except httpx.HTTPStatusError as e:
            logger.error(
                "RAG API rewind_messages failed: %s %s",
                e.response.status_code,
                e.response.text,
                exc_info=True,
            )
            raise
        except Exception as e:
            logger.error("RAG API rewind_messages error: %s", e, exc_info=True)
            raise


# 单例模式：全局唯一的 API 客户端实例
_rag_api_client: Optional[RAGAPIClient] = None


def get_rag_api_client() -> RAGAPIClient:
    """获取 RAG API 客户端单例"""
    global _rag_api_client
    if _rag_api_client is None:
        _rag_api_client = RAGAPIClient()
    return _rag_api_client

