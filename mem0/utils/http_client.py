"""HTTP client helpers for provider SDKs.

OpenAI's Cloudflare edge intermittently returns ``431 Request Header Fields Too
Large`` under concurrent load even when the request is tiny — the failing
requests are byte-for-byte identical to the succeeding ones (~650 bytes of
headers, far below any real 8-16 KB header limit), and an immediate retry
succeeds. So the 431 is a spurious, transient edge error, not a property of what
we send. (Cookies were investigated and ruled out: it fails with an empty cookie
jar just the same.)

The OpenAI Python SDK's retry logic only covers 408/409/429/5xx, so a single
spurious 431 turns into a hard failure for the whole ``add``/``search`` call.
``create_resilient_http_client`` builds an httpx client whose transport
transparently retries 431 (with a short backoff) before giving up, absorbing the
edge blips. Timeout and connection limits mirror the OpenAI SDK's own defaults,
so retrying 431 is the only behavioral change.
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

# Mirror the OpenAI Python SDK's own defaults (see openai._constants) so the only
# behavioral change is the 431 retry.
_DEFAULT_TIMEOUT = httpx.Timeout(timeout=600.0, connect=5.0)
_DEFAULT_LIMITS = httpx.Limits(max_connections=1000, max_keepalive_connections=100)

# Status codes the upstream SDK does NOT retry but which we've observed to be
# spurious/transient from OpenAI's edge.
_RETRY_STATUS = frozenset({431})
_MAX_RETRIES = 3
_BACKOFF_BASE = 0.25  # seconds; doubled each attempt: 0.25, 0.5, 1.0


def _backoff_seconds(attempt: int) -> float:
    return _BACKOFF_BASE * (2**attempt)


class _Retry431Transport(httpx.BaseTransport):
    """Sync transport wrapper that retries spurious transient status codes."""

    def __init__(self, inner: httpx.BaseTransport):
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(_MAX_RETRIES + 1):
            response = self._inner.handle_request(request)
            if response.status_code not in _RETRY_STATUS or attempt == _MAX_RETRIES:
                return response
            response.close()  # release the connection before retrying
            logger.warning(
                "Transient %d from %s; retrying (%d/%d)",
                response.status_code,
                request.url,
                attempt + 1,
                _MAX_RETRIES,
            )
            time.sleep(_backoff_seconds(attempt))
        return response  # unreachable, but keeps type checkers happy

    def close(self) -> None:
        self._inner.close()


class _AsyncRetry431Transport(httpx.AsyncBaseTransport):
    """Async counterpart of :class:`_Retry431Transport`."""

    def __init__(self, inner: httpx.AsyncBaseTransport):
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import anyio

        for attempt in range(_MAX_RETRIES + 1):
            response = await self._inner.handle_async_request(request)
            if response.status_code not in _RETRY_STATUS or attempt == _MAX_RETRIES:
                return response
            await response.aclose()
            logger.warning(
                "Transient %d from %s; retrying (%d/%d)",
                response.status_code,
                request.url,
                attempt + 1,
                _MAX_RETRIES,
            )
            await anyio.sleep(_backoff_seconds(attempt))
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def create_resilient_http_client() -> httpx.Client:
    """Build a sync httpx client that retries spurious transient 431s."""
    inner = httpx.HTTPTransport(limits=_DEFAULT_LIMITS)
    return httpx.Client(timeout=_DEFAULT_TIMEOUT, transport=_Retry431Transport(inner))


def create_resilient_async_http_client() -> httpx.AsyncClient:
    """Build an async httpx client that retries spurious transient 431s."""
    inner = httpx.AsyncHTTPTransport(limits=_DEFAULT_LIMITS)
    return httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, transport=_AsyncRetry431Transport(inner))
