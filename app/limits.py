import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.demo import _client_ip

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = 6 * 1024 * 1024      # the file plus multipart overhead
UPLOAD_PATH = "/documents/upload"

# In-memory sliding windows per (limit name, client IP). Per-process; reset on restart or sleep,
# so they only blunt bursts. The caps that must survive a sleep are DB-backed (routers/documents.py).
_hits: dict[tuple[str, str], deque] = defaultdict(deque)


def per_ip_limit(name: str, limit: int, window_seconds: int = 3600):
    """Dependency allowing `limit` requests per client IP per window."""
    def check(request: Request) -> None:
        now = time.monotonic()
        hits = _hits[(name, _client_ip(request))]
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(window_seconds - (now - hits[0])) + 1)
            raise HTTPException(
                status_code=429,
                detail=f"Too many {name} requests from this address. Try again in {-(-retry_after // 60)} min.",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)
    return check


async def upload_size_guard(request: Request, call_next):
    """Refuse an oversized upload from its Content-Length header, before the body is parsed."""
    if request.method == "POST" and request.url.path == UPLOAD_PATH:
        length = request.headers.get("content-length", "")
        if not length.isdigit():
            # Without a length the body size isn't known until it has all been read
            return JSONResponse(status_code=411, content={"detail": "Content-Length header is required"})
        if int(length) > MAX_REQUEST_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": f"File too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"},
            )
    return await call_next(request)
