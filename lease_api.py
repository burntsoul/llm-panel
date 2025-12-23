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
import asyncio
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Header, status, Request
from fastapi.responses import StreamingResponse, JSONResponse
import httpx

from config import settings
from lease import get_lease_manager, Lease
from llm_server import (
    ensure_llm_running_and_ready,
    llm_server_up,
    is_llm_ready,
    wait_for_llm_ready,
    touch_activity,
)
from proxmox import get_vm_status
from auth import verify_token


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")

# Global lock to ensure only one warmup sequence runs at a time
_warmup_lock = asyncio.Lock()
_warmup_status: Dict[str, Any] = {
    "in_progress": False,
    "result": None,  # True if successful, False if failed
}


def _extract_token(authorization: Optional[str]) -> Optional[str]:
    """Extract bearer token from Authorization header."""
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


async def _ensure_llm_ready_concurrent(timeout: int | None = None) -> bool:
    """
    Ensure LLM VM is running and ready.
    Only one warmup sequence runs at a time; others wait for result.

    Args:
        timeout: Warmup timeout in seconds

    Returns:
        True if LLM is ready, False otherwise
    """
    global _warmup_status

    async with _warmup_lock:
        # If already in progress, just wait
        if _warmup_status["in_progress"]:
            # This shouldn't happen with the lock, but just in case
            return _warmup_status.get("result", False)

        _warmup_status["in_progress"] = True
        try:
            # First ensure VM is running
            ok = await ensure_llm_running_and_ready(timeout)
            _warmup_status["result"] = ok
            return ok
        finally:
            _warmup_status["in_progress"] = False


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
        "status": "starting" | "ready",
        "llm_base_url": string,
        "retry_after_ms": int (if status is "starting"),
        "message": string
    }

    The status is "starting" if LLM is warming up, "ready" if it's operational.
    For "starting", the client should retry after retry_after_ms.
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

    # Start LLM warmup (non-blocking, concurrent)
    warmup_ok = await _ensure_llm_ready_concurrent(
        settings.LLM_READINESS_TIMEOUT
    )

    # Create or update lease
    lease = lease_mgr.create_lease(client_id, purpose, ttl_seconds)
    touch_activity()

    if warmup_ok:
        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content={
                "lease_id": lease.lease_id,
                "status": "ready",
                "llm_base_url": settings.LLM_BASE_URL,
                "message": "LLM is ready",
            },
        )
    else:
        # LLM is still starting or failed
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "lease_id": lease.lease_id,
                "status": "starting",
                "llm_base_url": settings.LLM_BASE_URL,
                "retry_after_ms": 5000,
                "message": "LLM is starting, please retry",
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

    # LLM readiness
    llm_ready = is_llm_ready()

    # Active leases
    lease_mgr = get_lease_manager()
    active_leases = len(lease_mgr.get_active_leases())

    ok = llm_ready and vm_status == "running"
    return {
        "ok": ok,
        "vm_state": vm_status,
        "llm_ready": llm_ready,
        "active_leases": active_leases,
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
    # Ensure LLM is ready
    llm_ready = is_llm_ready()
    if not llm_ready:
        logger.warning(
            f"Proxy request for {path} failed: LLM not ready "
            f"(lease_id={lease_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM server not ready",
        )

    # Construct target URL
    target_url = f"{settings.LLM_BASE_URL}{path}"

    # Read request body if present
    body = None
    if method.upper() in ("POST", "PUT", "PATCH"):
        try:
            body = await request.body()
        except Exception as e:
            logger.error(f"Failed to read proxy request body: {e}")
            body = b""

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

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.request(
                method=method,
                url=target_url,
                headers=headers,
                content=body,
            )

            # Touch activity
            touch_activity()

            # Handle streaming responses (e.g., from /v1/chat/completions with stream=true)
            if (
                "text/event-stream" in response.headers.get("content-type", "")
                or "application/x-ndjson" in response.headers.get("content-type", "")
            ):
                return StreamingResponse(
                    response.aiter_bytes(),
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.headers.get("content-type"),
                )
            else:
                # Non-streaming response
                return JSONResponse(
                    content=response.json() if response.text else {},
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )

    except httpx.TimeoutException:
        logger.error(f"Proxy timeout for {target_url} (lease_id={lease_id})")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="LLM server request timeout",
        )
    except httpx.HTTPError as e:
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
