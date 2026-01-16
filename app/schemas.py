"""Pydantic schemas for API validation."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator
from app.models import HttpMethod, ScheduleType, ScheduleStatus, RunStatus, ErrorType


# ============= Target Schemas =============

class TargetBase(BaseModel):
    """Base target schema."""
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., description="Target URL")
    method: HttpMethod = Field(default=HttpMethod.GET)
    headers: Dict[str, str] = Field(default_factory=dict)
    body_template: Optional[str] = Field(None, description="Optional request body template")
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    
    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL format."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class TargetCreate(TargetBase):
    """Schema for creating a target."""
    pass


class TargetUpdate(BaseModel):
    """Schema for updating a target."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    url: Optional[str] = None
    method: Optional[HttpMethod] = None
    headers: Optional[Dict[str, str]] = None
    body_template: Optional[str] = None
    timeout_seconds: Optional[float] = Field(None, ge=1.0, le=120.0)
    
    @field_validator("url")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        """Validate URL format."""
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class TargetResponse(TargetBase):
    """Target response schema."""
    id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# ============= Schedule Schemas =============

class ScheduleBase(BaseModel):
    """Base schedule schema."""
    name: str = Field(..., min_length=1, max_length=255)
    target_id: str
    schedule_type: ScheduleType = Field(default=ScheduleType.INTERVAL)
    interval_seconds: Optional[int] = Field(None, ge=1, description="Run every N seconds")
    cron_expression: Optional[str] = Field(None, description="Cron expression (e.g., '*/5 * * * *')")
    duration_seconds: Optional[int] = Field(None, ge=1, description="Stop after N seconds")
    max_runs: Optional[int] = Field(None, ge=1, description="Stop after N runs")
    
    @field_validator("interval_seconds")
    @classmethod
    def validate_interval(cls, v: Optional[int], info) -> Optional[int]:
        """Validate interval is provided for interval type."""
        return v
    
    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: Optional[str]) -> Optional[str]:
        """Validate cron expression format."""
        if v is not None:
            from croniter import croniter
            try:
                croniter(v)
            except (ValueError, KeyError) as e:
                raise ValueError(f"Invalid cron expression: {e}")
        return v


class ScheduleCreate(ScheduleBase):
    """Schema for creating a schedule."""
    
    @model_validator(mode='after')
    def validate_schedule_type_requirements(self) -> 'ScheduleCreate':
        """
        Validate schedule type matches provided fields.
        
        Edge cases handled:
        - Interval schedule requires interval_seconds
        - Cron schedule requires cron_expression
        - Duration should be greater than interval (warning-worthy but allowed)
        - Minimum interval of 1 second
        """
        if self.schedule_type == ScheduleType.INTERVAL:
            if not self.interval_seconds:
                raise ValueError("interval_seconds is required for interval schedule type")
            if self.interval_seconds < 1:
                raise ValueError("interval_seconds must be at least 1")
        
        if self.schedule_type == ScheduleType.CRON:
            if not self.cron_expression:
                raise ValueError("cron_expression is required for cron schedule type")
        
        # Warn if duration is less than interval (might only run once)
        if (self.duration_seconds and self.interval_seconds and 
            self.duration_seconds < self.interval_seconds):
            # Allow it but it will effectively run 0-1 times
            pass
        
        return self


class ScheduleUpdate(BaseModel):
    """Schema for updating a schedule."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    interval_seconds: Optional[int] = Field(None, ge=1)
    cron_expression: Optional[str] = None
    duration_seconds: Optional[int] = Field(None, ge=1)
    max_runs: Optional[int] = Field(None, ge=1)


class ScheduleResponse(BaseModel):
    """Schedule response schema."""
    id: str
    name: str
    target_id: str
    schedule_type: ScheduleType
    interval_seconds: Optional[int]
    cron_expression: Optional[str]
    duration_seconds: Optional[int]
    max_runs: Optional[int]
    status: ScheduleStatus
    started_at: Optional[datetime]
    expires_at: Optional[datetime]
    run_count: int
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# ============= Run Schemas =============

class AttemptResponse(BaseModel):
    """Attempt response schema."""
    id: str
    run_id: str
    attempt_number: int
    request_url: str
    request_method: HttpMethod
    request_headers: Dict[str, str]
    request_body: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]
    latency_ms: Optional[float]
    status_code: Optional[int]
    response_headers: Dict[str, str]
    response_body: Optional[str]
    response_size_bytes: Optional[int]
    error_type: ErrorType
    error_message: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class RunResponse(BaseModel):
    """Run response schema."""
    id: str
    schedule_id: str
    status: RunStatus
    scheduled_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    attempt_count: int
    final_status_code: Optional[int]
    final_error_type: ErrorType
    final_error_message: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class RunDetailResponse(RunResponse):
    """Run detail response with attempts."""
    attempts: List[AttemptResponse] = []


class RunListParams(BaseModel):
    """Query parameters for listing runs."""
    schedule_id: Optional[str] = None
    status: Optional[RunStatus] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


# ============= Metrics Schemas =============

class ScheduleMetrics(BaseModel):
    """Metrics for a schedule."""
    schedule_id: str
    schedule_name: str
    total_runs: int
    successful_runs: int
    failed_runs: int
    timeout_runs: int
    success_rate: float
    avg_latency_ms: Optional[float]
    last_run_at: Optional[datetime]


class GlobalMetrics(BaseModel):
    """Global system metrics."""
    total_targets: int
    total_schedules: int
    active_schedules: int
    paused_schedules: int
    total_runs: int
    runs_last_hour: int
    runs_last_24h: int
    success_rate_24h: float
    avg_latency_24h_ms: Optional[float]
    schedules: List[ScheduleMetrics] = []


# ============= Generic Response Schemas =============

class MessageResponse(BaseModel):
    """Generic message response."""
    message: str
    
    
class PaginatedResponse(BaseModel):
    """Paginated response wrapper."""
    items: List[Any]
    total: int
    limit: int
    offset: int
