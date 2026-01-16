"""Database models for the API Scheduler."""
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, String, Integer, Float, Text, DateTime, 
    ForeignKey, Enum, Boolean, JSON, Index
)
from sqlalchemy.orm import relationship, DeclarativeBase
from sqlalchemy.sql import func
import uuid


def generate_uuid() -> str:
    """Generate a UUID string."""
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class HttpMethod(str, enum.Enum):
    """Supported HTTP methods."""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"


class ScheduleType(str, enum.Enum):
    """Types of scheduling."""
    INTERVAL = "interval"  # Run every N seconds
    CRON = "cron"  # Run on cron expression


class ScheduleStatus(str, enum.Enum):
    """Schedule status."""
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"  # Window duration ended
    DELETED = "deleted"


class RunStatus(str, enum.Enum):
    """Run execution status."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ErrorType(str, enum.Enum):
    """Classification of errors."""
    NONE = "none"
    TIMEOUT = "timeout"
    DNS = "dns"
    CONNECTION = "connection"
    SSL = "ssl"
    CLIENT_ERROR = "4xx"  # 4xx responses
    SERVER_ERROR = "5xx"  # 5xx responses
    UNKNOWN = "unknown"


class Target(Base):
    """Represents an HTTP endpoint target."""
    __tablename__ = "targets"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    url = Column(Text, nullable=False)
    method = Column(Enum(HttpMethod), default=HttpMethod.GET)
    headers = Column(JSON, default=dict)  # {"Content-Type": "application/json"}
    body_template = Column(Text, nullable=True)  # Optional body, can include {{variables}}
    timeout_seconds = Column(Float, default=30.0)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relationships
    schedules = relationship("Schedule", back_populates="target", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_targets_name", "name"),
    )


class Schedule(Base):
    """Represents a schedule for executing requests."""
    __tablename__ = "schedules"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    target_id = Column(String(36), ForeignKey("targets.id"), nullable=False)
    
    # Scheduling type
    schedule_type = Column(Enum(ScheduleType), default=ScheduleType.INTERVAL)
    
    # For interval scheduling
    interval_seconds = Column(Integer, nullable=True)  # Run every N seconds
    
    # For cron scheduling
    cron_expression = Column(String(100), nullable=True)  # e.g., "*/5 * * * *"
    
    # Window constraints
    duration_seconds = Column(Integer, nullable=True)  # Stop after N seconds (optional)
    max_runs = Column(Integer, nullable=True)  # Stop after N runs (optional)
    
    # Status
    status = Column(Enum(ScheduleStatus), default=ScheduleStatus.ACTIVE)
    
    # Tracking
    started_at = Column(DateTime, nullable=True)  # When the schedule first started
    expires_at = Column(DateTime, nullable=True)  # When the window ends
    run_count = Column(Integer, default=0)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relationships
    target = relationship("Target", back_populates="schedules")
    runs = relationship("Run", back_populates="schedule", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_schedules_status", "status"),
        Index("ix_schedules_next_run", "next_run_at"),
        Index("ix_schedules_target", "target_id"),
    )


class Run(Base):
    """Represents a single scheduled execution."""
    __tablename__ = "runs"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    schedule_id = Column(String(36), ForeignKey("schedules.id"), nullable=False)
    
    # Idempotency key to prevent duplicate runs
    # Format: {schedule_id}:{timestamp_bucket} where bucket is floored to second
    idempotency_key = Column(String(100), unique=True, nullable=True)
    
    # Execution tracking
    status = Column(Enum(RunStatus), default=RunStatus.PENDING)
    scheduled_at = Column(DateTime, nullable=False)  # When it was supposed to run
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    # Result summary
    attempt_count = Column(Integer, default=0)
    final_status_code = Column(Integer, nullable=True)
    final_error_type = Column(Enum(ErrorType), default=ErrorType.NONE)
    final_error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=func.now())
    
    # Relationships
    schedule = relationship("Schedule", back_populates="runs")
    attempts = relationship("Attempt", back_populates="run", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_runs_schedule", "schedule_id"),
        Index("ix_runs_status", "status"),
        Index("ix_runs_scheduled_at", "scheduled_at"),
    )


class Attempt(Base):
    """Represents a single HTTP request attempt within a run."""
    __tablename__ = "attempts"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    run_id = Column(String(36), ForeignKey("runs.id"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    
    # Request details
    request_url = Column(Text, nullable=False)
    request_method = Column(Enum(HttpMethod), nullable=False)
    request_headers = Column(JSON, default=dict)
    request_body = Column(Text, nullable=True)
    
    # Timing
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    latency_ms = Column(Float, nullable=True)  # Response time in milliseconds
    
    # Response details
    status_code = Column(Integer, nullable=True)
    response_headers = Column(JSON, default=dict)
    response_body = Column(Text, nullable=True)  # First N bytes
    response_size_bytes = Column(Integer, nullable=True)
    
    # Error classification
    error_type = Column(Enum(ErrorType), default=ErrorType.NONE)
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=func.now())
    
    # Relationships
    run = relationship("Run", back_populates="attempts")
    
    __table_args__ = (
        Index("ix_attempts_run", "run_id"),
    )
