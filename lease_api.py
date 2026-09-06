# lease_api.py
"""
Lease + Proxy API endpoints for /v1 routes.

Provides:
- Lease management (create, get, refresh, release)
- Health checks with lease info
- HTTP proxying to LLM server with request/response forwarding
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Header, status, Request
from fastapi.responses import StreamingResponse, JSONResponse, Response
import httpx
import json

from config import settings
from lease import get_lease_manager, Lease
from llm_server import touch_activity
# Compatibility exports for older integrations that monkeypatch these names.
# Inference and lease creation no longer call either helper.
from llm_server import ensure_llm_running_and_ready, is_llm_ready
from proxmox import get_vm_status
from auth import verify_token
from llm_runtime_backends import resolve_runtime_target
from llm_runtime_manager import (
    RuntimeRequestCancelled,
    RuntimeTargetError,
    cancelled_error_payload,
    get_runtime_scheduler,
    sse_cancelled_payload,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")

_RESPONSE_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

_RESPONSE_FRAMING_HEADERS = {
    "content-length",
    "content-encoding",
}

_RESPONSE_AGENT_MANAGED_HEADERS = {
    "server",
    "date",
}


def _extract_token(authorization: Optional[str]) -> Optional[str]:
    """Extract bearer token from Authorization header."""
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def _sanitize_upstream_response_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """
    Remove upstream headers that can break proxy framing or conflict with ASGI server headers.
    """
    blocked = (
        _RESPONSE_HOP_BY_HOP_HEADERS
        | _RESPONSE_FRAMING_HEADERS
        | _RESPONSE_AGENT_MANAGED_HEADERS
    )
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in blocked
    }


# ============================================================================
# Lease Endpoints
# ============================================================================


@router.post("/lease")
async def create_lease(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """
    Create or get a lease for LLM access.

    Body: { "client_id": string, "purpose": string, "ttl_seconds": int }

    Returns: {
        "lease_id": string,
        "status": "ready",
        "llm_base_url": string,
        "message": string
    }

    Lease creation is resource-free. Model selection, queueing and startup happen
    on the subsequent inference request.
    """
    # Auth
    token = _extract_token(authorization)
    if not verify_token(token):
        logger.warning("POST /v1/lease: auth failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )

    # Parse body
    try:
        body = await request.json()
        client_id = body.get("client_id", "")
        purpose = body.get("purpose", "")
        ttl_seconds = body.get("ttl_seconds", settings.LEASE_DEFAULT_TTL)

        if not client_id or not purpose:
            raise ValueError("client_id and purpose are required")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
    except Exception as e:
        logger.warning(f"POST /v1/lease: invalid request: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request: {str(e)}",
        )

    lease_mgr = get_lease_manager()

    # Create or update lease
    lease = lease_mgr.create_lease(client_id, purpose, ttl_seconds)
    touch_activity()

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "lease_id": lease.lease_id,
            "status": "ready",
            "llm_base_url": str(request.base_url).rstrip("/"),
            "message": "Lease is ready; model loading occurs on the inference request",
        },
    )


@router.get("/lease/{lease_id}")
async def get_lease(
    lease_id: str,
    authorization: Optional[str] = Header(None),
):
    """
    Get current lease status.

    Returns: {
        "lease_id": string,
        "client_id": string,
        "purpose": string,
        "status": "ready" | "expired",
        "ttl_seconds": int,
        "created_at": string (ISO 8601),
        "last_seen": string (ISO 8601),
        "expires_at": string (ISO 8601)
    }
    """
    # Auth
    token = _extract_token(authorization)
    if not verify_token(token):
        logger.warning(f"GET /v1/lease/{lease_id}: auth failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )

    lease_mgr = get_lease_manager()
    lease = lease_mgr.get_lease(lease_id)

    if lease is None:
        logger.warning(f"GET /v1/lease/{lease_id}: lease not found or expired")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lease not found or expired",
        )

    return {
        "lease_id": lease.lease_id,
        "client_id": lease.client_id,
        "purpose": lease.purpose,
        "status": "ready",
        "ttl_seconds": lease.ttl_seconds,
        "created_at": lease.created_at.isoformat(),
        "last_seen": lease.last_seen.isoformat(),
        "expires_at": lease.expires_at.isoformat(),
    }


@router.post("/lease/{lease_id}/refresh")
async def refresh_lease(
    lease_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """
    Refresh a lease (extend expiry and update last_seen).

    Body: { "ttl_seconds": int } (optional; uses current TTL if not provided)

    Returns: updated lease info
    """
    # Auth
    token = _extract_token(authorization)
    if not verify_token(token):
        logger.warning(f"POST /v1/lease/{lease_id}/refresh: auth failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )

    # Parse body
    ttl_seconds = None
    try:
        body = await request.json()
        ttl_seconds = body.get("ttl_seconds")
    except Exception:
        pass

    lease_mgr = get_lease_manager()
    ok = lease_mgr.refresh_lease(lease_id, ttl_seconds)

    if not ok:
        logger.warning(f"POST /v1/lease/{lease_id}/refresh: lease not found or expired")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lease not found or expired",
        )

    lease = lease_mgr.get_lease(lease_id)
    
    if lease is None:
        logger.warning(f"POST /v1/lease/{lease_id}/refresh: lease disappeared after refresh")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lease not found or expired",
        )
    
    touch_activity()

    return {
        "lease_id": lease.lease_id,
        "client_id": lease.client_id,
        "purpose": lease.purpose,
        "status": "ready",
        "ttl_seconds": lease.ttl_seconds,
        "created_at": lease.created_at.isoformat(),
        "last_seen": lease.last_seen.isoformat(),
        "expires_at": lease.expires_at.isoformat(),
    }


@router.post("/lease/{lease_id}/release")
async def release_lease(
    lease_id: str,
    authorization: Optional[str] = Header(None),
):
    """
    Release a lease (remove immediately).

    Returns: { "success": bool }
    """
    # Auth
    token = _extract_token(authorization)
    if not verify_token(token):
        logger.warning(f"POST /v1/lease/{lease_id}/release: auth failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )

    lease_mgr = get_lease_manager()
    ok = lease_mgr.release_lease(lease_id)

    if not ok:
        logger.warning(f"POST /v1/lease/{lease_id}/release: lease not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lease not found",
        )

    return {"success": True, "message": f"Lease {lease_id} released"}


# ============================================================================
# Health Endpoint
# ============================================================================


@router.get("/health")
async def health_check(authorization: Optional[str] = Header(None)):
    """
    Health check with detailed system and lease information.

    Returns: {
        "ok": bool,
        "vm_state": string ("running" | "stopped" | "unknown"),
        "llm_ready": bool,
        "active_leases": int,
        "message": string
    }
    """
    # Auth (optional for health)
    token = _extract_token(authorization)
    if settings.LLM_AGENT_TOKEN and not verify_token(token):
        logger.warning("GET /v1/health: auth failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )

    # VM status
    try:
        vm_status = get_vm_status(settings.LLM_VM_ID)
    except Exception as e:
        vm_status = f"unknown ({e})"

    scheduler_snapshot = get_runtime_scheduler().snapshot()
    llm_ready = bool(scheduler_snapshot.get("accepting"))

    # Active leases
    lease_mgr = get_lease_manager()
    active_leases = len(lease_mgr.get_active_leases())

    ok = llm_ready
    return {
        "ok": ok,
        "vm_state": vm_status,
        "llm_ready": llm_ready,
        "active_leases": active_leases,
        "scheduler_phase": scheduler_snapshot.get("phase"),
        "active_target": scheduler_snapshot.get("active_target"),
        "message": "All systems operational" if ok else "System degraded",
    }


# ============================================================================
# Proxy Endpoint
# ============================================================================


async def _proxy_forward(
    method: str,
    path: str,
    request: Request,
    lease_id: Optional[str],
) -> StreamingResponse | JSONResponse:
    """
    Forward an HTTP request to the LLM server.

    Args:
        method: HTTP method (GET, POST, etc.)
        path: Request path (relative to LLM base URL)
        request: Original FastAPI request
        lease_id: Optional lease ID for tracking

    Returns:
        Response (streaming or JSON)
    """
    # Read request body if present
    body = None
    if method.upper() in ("POST", "PUT", "PATCH"):
        try:
            body = await request.body()
        except Exception as e:
            logger.error(f"Failed to read proxy request body: {e}")
            body = b""

    normalized = "/" + path.lstrip("/")
    inference_paths = {
        "/api/generate": "generate",
        "/api/chat": "generate",
        "/api/embed": "embed",
        "/api/embeddings": "embed",
        "/v1/chat/completions": "generate",
        "/v1/completions": "generate",
        "/v1/responses": "generate",
        "/v1/embeddings": "embed",
    }
    workload = inference_paths.get(normalized.rstrip("/"))
    target = None
    if workload:
        try:
            payload = json.loads((body or b"").decode("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Inference proxy body must be JSON") from exc
        model = payload.get("model") if isinstance(payload, dict) else None
        if not isinstance(model, str) or not model.strip():
            raise HTTPException(status_code=400, detail="Inference proxy requests require a model")
        try:
            target, _public, upstream = resolve_runtime_target(model, workload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if target.provider == "llama_cpp" and normalized.startswith("/api/"):
            raise HTTPException(status_code=400, detail="llama.cpp profiles require an OpenAI-compatible proxy path")
        payload["model"] = upstream
        if target.provider == "ollama":
            payload["keep_alive"] = -1
        body = json.dumps(payload).encode("utf-8")

    target_url = f"{(target.base_url if target else settings.LLM_BASE_URL).rstrip('/')}{normalized}"

    # Forward headers (skip hop-by-hop and auth headers)
    skip_headers = {
        "host",
        "connection",
        "transfer-encoding",
        "content-length",
        "authorization",
    }
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in skip_headers
    }

    logger.info(
        f"Proxy: {method} {path} -> {target_url} "
        f"(lease_id={lease_id})"
    )

    permit = None
    client = httpx.AsyncClient(timeout=None)
    response = None
    try:
        if target is not None:
            permit = await get_runtime_scheduler().acquire(
                target,
                endpoint=f"/v1/proxy{normalized}",
                client_id=(request.headers.get("x-client-id") or lease_id or (request.client.host if request.client else "unknown")),
                stream=bool(workload and json.loads((body or b"{}").decode("utf-8")).get("stream")),
            )
        built = client.build_request(method=method, url=target_url, headers=headers, content=body)
        response = await (permit.run(client.send(built, stream=True)) if permit else client.send(built, stream=True))

        if response.status_code >= 400:
            logger.warning("Proxy upstream non-2xx response path=%s status=%s", path, response.status_code)
        touch_activity()

        # Handle streaming responses (e.g., from /v1/chat/completions with stream=true)
        sanitized_headers = _sanitize_upstream_response_headers(dict(response.headers))
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type or "application/x-ndjson" in content_type:
            async def generate():
                runtime_error = None
                try:
                    iterator = response.aiter_bytes().__aiter__()
                    while True:
                        try:
                            chunk = await (permit.run(iterator.__anext__()) if permit else iterator.__anext__())
                        except StopAsyncIteration:
                            break
                        yield chunk
                except RuntimeRequestCancelled as exc:
                    if "text/event-stream" in content_type:
                        yield sse_cancelled_payload(exc)
                    else:
                        yield json.dumps(cancelled_error_payload(exc)).encode("utf-8") + b"\n"
                except (RuntimeTargetError, httpx.HTTPError) as exc:
                    runtime_error = str(exc)
                    payload = {"error": {"message": str(exc), "type": "runtime_target_error"}}
                    if "text/event-stream" in content_type:
                        yield b"data: " + json.dumps(payload).encode("utf-8") + b"\n\ndata: [DONE]\n\n"
                    else:
                        yield json.dumps(payload).encode("utf-8") + b"\n"
                finally:
                    await response.aclose()
                    await client.aclose()
                    if permit:
                        await permit.release(error=runtime_error)
            return StreamingResponse(
                generate(), status_code=response.status_code,
                headers=sanitized_headers, media_type=sanitized_headers.get("content-type"),
            )

        content = await (permit.run(response.aread()) if permit else response.aread())
        result = Response(content=content, status_code=response.status_code, headers=sanitized_headers)
        await response.aclose()
        await client.aclose()
        if permit:
            await permit.release()
        return result

    except RuntimeRequestCancelled as exc:
        if response is not None:
            await response.aclose()
        await client.aclose()
        if permit:
            await permit.release()
        return JSONResponse(status_code=409, content=cancelled_error_payload(exc))
    except RuntimeTargetError as exc:
        if response is not None:
            await response.aclose()
        await client.aclose()
        if permit:
            await permit.release(error=str(exc))
        return JSONResponse(status_code=502, content={"error": {"message": str(exc), "type": "runtime_target_error"}})
    except httpx.TimeoutException:
        if response is not None:
            await response.aclose()
        await client.aclose()
        if permit:
            await permit.release(error="upstream timeout")
        logger.error(f"Proxy timeout for {target_url} (lease_id={lease_id})")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="LLM server request timeout",
        )
    except httpx.HTTPError as e:
        if response is not None:
            await response.aclose()
        await client.aclose()
        if permit:
            await permit.release(error=str(e))
        logger.error(
            f"Proxy HTTP error for {target_url} (lease_id={lease_id}): {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM server error",
        )


@router.api_route("/proxy/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(
    path: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_lease_id: Optional[str] = Header(None),
):
    """
    Proxy HTTP requests to the LLM server.

    Requirements:
    - Valid Authorization header with Bearer token
    - Optional X-Lease-Id header to track request to a specific lease
      (if provided, lease must be valid and not expired)
    - LLM server must be ready

    Request path is forwarded as-is to the LLM server.

    Supports:
    - Streaming responses (Server-Sent Events, NDJSON)
    - All HTTP methods (GET, POST, etc.)
    - All content types
    """
    # Auth
    token = _extract_token(authorization)
    if not verify_token(token):
        logger.warning(f"Proxy request auth failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )

    # Validate lease if provided
    if x_lease_id:
        lease_mgr = get_lease_manager()
        lease = lease_mgr.get_lease(x_lease_id)
        if lease is None:
            logger.warning(f"Proxy: invalid or expired lease {x_lease_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Lease not found or expired",
            )
        # Refresh lease to extend activity
        lease_mgr.refresh_lease(x_lease_id)

    return await _proxy_forward(
        method=request.method,
        path=f"/{path}",
        request=request,
        lease_id=x_lease_id,
    )
