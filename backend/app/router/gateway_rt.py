"""
Gateway/BFF routes for proxying internal microservices.
"""
from __future__ import annotations

import uuid
from typing import Dict, Iterable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from core.config import settings
from models.user import User
from service.auth import get_current_user, get_current_user_optional_query_token
from utils.get_logger import log


router = APIRouter()

_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _build_trace_id(request: Request) -> str:
    """Resolve trace id from headers or generate a new one."""
    return (
        request.headers.get("X-Trace-Id")
        or request.headers.get("X-Request-ID")
        or str(uuid.uuid4())
    )


def _filter_request_headers(
    request: Request, user_id: int, trace_id: str
) -> Dict[str, str]:
    """Build outbound headers for proxy calls."""
    headers: Dict[str, str] = {}
    for name, value in request.headers.items():
        lname = name.lower()
        if lname in _HOP_HEADERS:
            continue
        if lname in {"host", "content-length", "x-user-id", "authorization"}:
            continue
        headers[name] = value
    headers["X-User-Id"] = str(user_id)
    headers["X-Trace-Id"] = trace_id
    return headers


def _filter_response_headers(headers: Iterable[tuple[str, str]]) -> Dict[str, str]:
    """Drop hop-by-hop headers from upstream response."""
    filtered: Dict[str, str] = {}
    for name, value in headers:
        lname = name.lower()
        if lname in _HOP_HEADERS:
            continue
        if lname == "content-length":
            continue
        filtered[name] = value
    return filtered


async def _close_upstream(response: httpx.Response, client: httpx.AsyncClient) -> None:
    """Close upstream response and client."""
    await response.aclose()
    await client.aclose()


async def _proxy_request(
    request: Request,
    upstream_url: str,
    current_user: User,
) -> Response:
    """Proxy the incoming request to a target upstream service."""
    trace_id = _build_trace_id(request)
    headers = _filter_request_headers(request, current_user.id, trace_id)
    params = request.query_params

    async def _iter_body():
        async for chunk in request.stream():
            yield chunk

    content = None
    if request.method not in {"GET", "HEAD"}:
        content = _iter_body()

    timeout = httpx.Timeout(30.0, read=None)
    client = httpx.AsyncClient(timeout=timeout)
    try:
        upstream_request = client.build_request(
            request.method,
            upstream_url,
            params=params,
            headers=headers,
            content=content,
        )
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        log.error("Gateway proxy failed: %s", exc)
        raise HTTPException(status_code=502, detail="Upstream service unavailable") from exc

    response_headers = _filter_response_headers(upstream_response.headers.items())
    response_headers["X-Trace-Id"] = trace_id

    return StreamingResponse(
        upstream_response.aiter_raw(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        background=BackgroundTask(_close_upstream, upstream_response, client),
    )


@router.api_route(
    "/deep-research/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_deep_research(
    request: Request,
    path: str,
    current_user: User = Depends(get_current_user_optional_query_token),
):
    """Proxy DeepResearch service requests."""
    base = settings.DEEP_RESEARCH_SERVICE_URL.rstrip("/")
    normalized_path = (path or "").lstrip("/")
    if not normalized_path:
        upstream_path = "/api/deep-research"
    elif normalized_path == "deep-research" or normalized_path.startswith("deep-research/"):
        # Backward-compatible form: /api/deep-research/deep-research/...
        upstream_path = f"/api/{normalized_path}"
    elif normalized_path.startswith("idea-generation") or normalized_path.startswith("notebook"):
        # IdeaGen/Notebook endpoints live under /api/*
        upstream_path = f"/api/{normalized_path}"
    else:
        # Canonical DeepResearch form: /api/deep-research/...
        upstream_path = f"/api/deep-research/{normalized_path}"
    return await _proxy_request(request, f"{base}{upstream_path}", current_user)


@router.get(
    "/doc-studio/workspaces/{workspace_id}/edit/async/{run_id}/events",
)
async def proxy_doc_studio_async_events(
    request: Request,
    workspace_id: str,
    run_id: str,
    current_user: User = Depends(get_current_user_optional_query_token),
):
    """Proxy Doc Studio async event stream (supports query token)."""
    base = settings.DOC_STUDIO_SERVICE_URL.rstrip("/")
    upstream_path = f"/api/workspaces/{workspace_id}/edit/async/{run_id}/events"
    return await _proxy_request(request, f"{base}{upstream_path}", current_user)


@router.api_route(
    "/doc-studio/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_doc_studio(
    request: Request,
    path: str,
    current_user: User = Depends(get_current_user),
):
    """Proxy Doc Studio service requests."""
    base = settings.DOC_STUDIO_SERVICE_URL.rstrip("/")
    upstream_path = f"/api/{path}" if path else "/api"
    return await _proxy_request(request, f"{base}{upstream_path}", current_user)
