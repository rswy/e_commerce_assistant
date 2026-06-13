"""API key authentication dependency for FastAPI.

Usage:
    @app.post("/query")
    async def query(request: QueryRequest, _: bool = Depends(verify_api_key)):
        ...

Authentication is disabled when API_KEY is an empty string (development mode).
In production, set the API_KEY environment variable to a strong random secret
and pass the key in the X-API-Key request header.
"""

from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

from app.config import API_KEY

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str | None = Security(api_key_header)) -> bool:
    """FastAPI dependency that validates the X-API-Key header.

    If API_KEY config is empty, authentication is disabled and every request
    is allowed through (development / local mode).

    Args:
        key: Value of the X-API-Key header, or None if not provided.

    Returns:
        True when the request is authenticated.

    Raises:
        HTTPException 403: When API_KEY is set but the provided key is wrong
            or missing.
    """
    if not API_KEY:
        # Auth disabled — pass through unconditionally.
        return True

    if key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )

    return True
