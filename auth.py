# auth.py
"""
Token-based authentication for lease and proxy endpoints.

Uses a shared secret configured via LLM_AGENT_TOKEN env var or config.
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Callable, Optional

from fastapi import HTTPException, Header, status
from config import settings


logger = logging.getLogger(__name__)


def verify_token(token: Optional[str] = None) -> bool:
    """Verify if a token is valid.

    Args:
        token: The token to verify

    Returns:
        True if valid, False otherwise
    """
    if not settings.LLM_AGENT_TOKEN:
        # If no token configured, skip auth
        logger.warning("LLM_AGENT_TOKEN not configured; auth disabled")
        return True

    if token is None:
        return False

    return token == settings.LLM_AGENT_TOKEN


def require_token(func: Callable) -> Callable:
    """Decorator to require a valid token in Authorization header.

    Usage:
        @app.post("/v1/lease")
        @require_token
        def create_lease(..):
            ...
    """

    @wraps(func)
    def wrapper(*args, authorization: Optional[str] = Header(None), **kwargs):
        # Extract bearer token from "Bearer <token>"
        token = None
        if authorization:
            parts = authorization.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1]

        if not verify_token(token):
            logger.warning(f"Auth failed; invalid or missing token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing authentication token",
            )

        # Remove authorization from kwargs to avoid passing it to the endpoint
        kwargs.pop("authorization", None)
        return func(*args, **kwargs)

    return wrapper


def require_token_async(func: Callable) -> Callable:
    """Async version of require_token decorator."""

    @wraps(func)
    async def wrapper(*args, authorization: Optional[str] = Header(None), **kwargs):
        # Extract bearer token
        token = None
        if authorization:
            parts = authorization.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1]

        if not verify_token(token):
            logger.warning(f"Auth failed; invalid or missing token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing authentication token",
            )

        kwargs.pop("authorization", None)
        return await func(*args, **kwargs)

    return wrapper
