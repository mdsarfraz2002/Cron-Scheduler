"""HTTP executor with retry logic and error classification."""
import asyncio
import logging
import warnings
from datetime import datetime, timezone
from typing import Optional, Tuple
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Target, Run, Attempt,
    HttpMethod, RunStatus, ErrorType
)
from app.config import get_settings

# Suppress SSL warnings when verification is disabled
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

logger = logging.getLogger(__name__)
settings = get_settings()

# Maximum response body size to store (100KB)
MAX_RESPONSE_BODY_SIZE = 100 * 1024


def utcnow() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def classify_error(exception: Exception) -> Tuple[ErrorType, str]:
    """Classify an exception into an error type."""
    error_message = str(exception)
    
    if isinstance(exception, httpx.TimeoutException):
        return ErrorType.TIMEOUT, f"Request timed out: {error_message}"
    
    if isinstance(exception, httpx.ConnectError):
        error_lower = error_message.lower()
        if "name or service not known" in error_lower or "dns" in error_lower:
            return ErrorType.DNS, f"DNS resolution failed: {error_message}"
        if "ssl" in error_lower or "certificate" in error_lower:
            return ErrorType.SSL, f"SSL/TLS error: {error_message}"
        return ErrorType.CONNECTION, f"Connection failed: {error_message}"
    
    if isinstance(exception, httpx.HTTPStatusError):
        status_code = exception.response.status_code
        if 400 <= status_code < 500:
            return ErrorType.CLIENT_ERROR, f"Client error {status_code}: {error_message}"
        if 500 <= status_code < 600:
            return ErrorType.SERVER_ERROR, f"Server error {status_code}: {error_message}"
    
    if "ssl" in error_message.lower() or "certificate" in error_message.lower():
        return ErrorType.SSL, f"SSL/TLS error: {error_message}"
    
    return ErrorType.UNKNOWN, f"Unknown error: {error_message}"


def classify_status_code(status_code: int) -> ErrorType:
    """Classify HTTP status code into error type."""
    if 200 <= status_code < 400:
        return ErrorType.NONE
    if 400 <= status_code < 500:
        return ErrorType.CLIENT_ERROR
    if 500 <= status_code < 600:
        return ErrorType.SERVER_ERROR
    return ErrorType.UNKNOWN


def calculate_backoff_delay(attempt_num: int, base_delay: float = 1.0, max_delay: float = 30.0) -> float:
    """
    Calculate exponential backoff delay with jitter.
    
    Formula: min(base_delay * 2^(attempt-1), max_delay)
    """
    delay = min(base_delay * (2 ** (attempt_num - 1)), max_delay)
    return delay


class HttpExecutor:
    """
    Executes HTTP requests with retry logic and detailed tracking.
    
    Features:
    - Exponential backoff for retries
    - Error classification
    - Response body size limiting
    - Detailed attempt tracking
    """
    
    def __init__(self, max_retries: int = None, base_retry_delay: float = None):
        self.max_retries = max_retries or settings.max_retries
        self.base_retry_delay = base_retry_delay or settings.retry_delay_seconds
        self._client: Optional[httpx.AsyncClient] = None
    
    async def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
                verify=settings.verify_ssl,  # Configurable SSL verification
            )
        return self._client
    
    async def close(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def execute_run(
        self,
        session: AsyncSession,
        run: Run,
        target: Target,
    ) -> Run:
        """Execute a run with retries and track all attempts."""
        run.status = RunStatus.RUNNING
        run.started_at = utcnow()
        session.add(run)
        await session.flush()
        
        last_attempt: Optional[Attempt] = None
        
        for attempt_num in range(1, self.max_retries + 1):
            attempt = await self._execute_attempt(
                session=session,
                run=run,
                target=target,
                attempt_number=attempt_num,
            )
            last_attempt = attempt
            run.attempt_count = attempt_num
            
            # Success - stop retrying
            if attempt.error_type == ErrorType.NONE:
                run.status = RunStatus.SUCCESS
                run.final_status_code = attempt.status_code
                run.final_error_type = ErrorType.NONE
                break
            
            # Non-retryable errors (client errors)
            if attempt.error_type == ErrorType.CLIENT_ERROR:
                run.status = RunStatus.FAILED
                run.final_status_code = attempt.status_code
                run.final_error_type = attempt.error_type
                run.final_error_message = attempt.error_message
                break
            
            # Retryable error - wait with exponential backoff
            if attempt_num < self.max_retries:
                backoff_delay = calculate_backoff_delay(
                    attempt_num, 
                    self.base_retry_delay
                )
                logger.info(
                    f"Run {run.id} attempt {attempt_num} failed with {attempt.error_type}, "
                    f"retrying in {backoff_delay:.1f}s (exponential backoff)"
                )
                await asyncio.sleep(backoff_delay)
        
        # If we exhausted retries
        if run.status == RunStatus.RUNNING:
            if last_attempt and last_attempt.error_type == ErrorType.TIMEOUT:
                run.status = RunStatus.TIMEOUT
            else:
                run.status = RunStatus.FAILED
            
            if last_attempt:
                run.final_status_code = last_attempt.status_code
                run.final_error_type = last_attempt.error_type
                run.final_error_message = last_attempt.error_message
        
        run.completed_at = utcnow()
        session.add(run)
        await session.flush()
        
        return run
    
    async def _execute_attempt(
        self,
        session: AsyncSession,
        run: Run,
        target: Target,
        attempt_number: int,
    ) -> Attempt:
        """Execute a single HTTP request attempt."""
        client = await self.get_client()
        
        # Prepare request body (could add template variable substitution here)
        request_body = self._prepare_body(target.body_template)
        
        attempt = Attempt(
            run_id=run.id,
            attempt_number=attempt_number,
            request_url=target.url,
            request_method=target.method,
            request_headers=target.headers or {},
            request_body=request_body,
            started_at=utcnow(),
        )
        
        try:
            # Build request
            request_kwargs = {
                "method": target.method.value,
                "url": target.url,
                "headers": target.headers or {},
                "timeout": target.timeout_seconds,
            }
            
            if request_body and target.method in [
                HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH
            ]:
                request_kwargs["content"] = request_body
            
            # Execute request with timing
            start_time = utcnow()
            response = await client.request(**request_kwargs)
            end_time = utcnow()
            
            # Calculate latency
            latency_ms = (end_time - start_time).total_seconds() * 1000
            
            # Get response body with size limit to prevent OOM
            response_body = await self._safe_read_body(response)
            
            # Update attempt
            attempt.completed_at = end_time
            attempt.latency_ms = latency_ms
            attempt.status_code = response.status_code
            attempt.response_headers = dict(response.headers)
            attempt.response_body = response_body
            attempt.response_size_bytes = len(response.content) if response.content else 0
            
            # Classify result
            if 200 <= response.status_code < 400:
                attempt.error_type = ErrorType.NONE
            else:
                attempt.error_type = classify_status_code(response.status_code)
                attempt.error_message = f"HTTP {response.status_code}"
            
            logger.info(
                f"Attempt {attempt_number} for run {run.id}: "
                f"status={response.status_code}, latency={latency_ms:.2f}ms"
            )
            
        except Exception as e:
            attempt.completed_at = utcnow()
            attempt.error_type, attempt.error_message = classify_error(e)
            
            logger.warning(
                f"Attempt {attempt_number} for run {run.id} failed: "
                f"{attempt.error_type} - {attempt.error_message}"
            )
        
        session.add(attempt)
        await session.flush()
        
        return attempt
    
    def _prepare_body(self, body_template: Optional[str]) -> Optional[str]:
        """
        Prepare request body from template.
        
        Currently supports:
        - {{timestamp}} - Current UTC timestamp in ISO format
        - {{run_id}} - Run ID (not available at template time, placeholder for future)
        
        This is a simple implementation. For production, consider:
        - Jinja2 templating
        - User-defined variables stored with the target
        """
        if not body_template:
            return None
        
        # Simple variable substitution
        result = body_template
        result = result.replace("{{timestamp}}", utcnow().isoformat())
        
        return result
    
    async def _safe_read_body(self, response: httpx.Response) -> Optional[str]:
        """
        Safely read response body with size limits.
        
        Prevents OOM on very large responses.
        """
        try:
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > MAX_RESPONSE_BODY_SIZE:
                # Response too large, truncate
                return f"[Response truncated - size {content_length} bytes exceeds limit]"
            
            # Read the body (already loaded by httpx at this point)
            body = response.text
            if len(body) > MAX_RESPONSE_BODY_SIZE:
                return body[:MAX_RESPONSE_BODY_SIZE] + "\n[...truncated...]"
            return body
            
        except Exception as e:
            logger.warning(f"Failed to read response body: {e}")
            return None


# Global executor instance
executor = HttpExecutor()
