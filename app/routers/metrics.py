"""API routes for metrics and observability."""
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select, func, and_, case
from sqlalchemy.ext.asyncio import AsyncSession
import pytz

from app.database import get_db
from app.models import Target, Schedule, Run, Attempt, ScheduleStatus, RunStatus, ErrorType
from app.schemas import GlobalMetrics, ScheduleMetrics

# IST timezone
IST = pytz.timezone('Asia/Kolkata')


def now_ist() -> datetime:
    """Get current IST time as naive datetime for database storage."""
    return datetime.now(IST).replace(tzinfo=None)

router = APIRouter(tags=["Metrics"])


@router.get("/metrics", response_model=GlobalMetrics)
async def get_metrics(
    db: AsyncSession = Depends(get_db),
):
    """
    Get global system metrics.
    
    Returns aggregated statistics including:
    - Total counts of targets, schedules, runs
    - Success rates
    - Average latency
    - Per-schedule breakdown
    """
    now = now_ist()
    one_hour_ago = now - timedelta(hours=1)
    one_day_ago = now - timedelta(hours=24)
    
    # Count targets
    target_count = await db.execute(select(func.count(Target.id)))
    total_targets = target_count.scalar() or 0
    
    # Count schedules by status
    schedule_counts = await db.execute(
        select(Schedule.status, func.count(Schedule.id))
        .group_by(Schedule.status)
    )
    schedule_stats = dict(schedule_counts.fetchall())
    
    total_schedules = sum(schedule_stats.values())
    active_schedules = schedule_stats.get(ScheduleStatus.ACTIVE, 0)
    paused_schedules = schedule_stats.get(ScheduleStatus.PAUSED, 0)
    
    # Total runs
    total_runs_result = await db.execute(select(func.count(Run.id)))
    total_runs = total_runs_result.scalar() or 0
    
    # Runs in last hour
    runs_last_hour_result = await db.execute(
        select(func.count(Run.id))
        .where(Run.scheduled_at >= one_hour_ago)
    )
    runs_last_hour = runs_last_hour_result.scalar() or 0
    
    # Runs in last 24 hours
    runs_last_24h_result = await db.execute(
        select(func.count(Run.id))
        .where(Run.scheduled_at >= one_day_ago)
    )
    runs_last_24h = runs_last_24h_result.scalar() or 0
    
    # Success rate in last 24 hours
    success_count_result = await db.execute(
        select(func.count(Run.id))
        .where(
            and_(
                Run.scheduled_at >= one_day_ago,
                Run.status == RunStatus.SUCCESS,
            )
        )
    )
    success_count = success_count_result.scalar() or 0
    success_rate_24h = (success_count / runs_last_24h * 100) if runs_last_24h > 0 else 0.0
    
    # Average latency in last 24 hours
    avg_latency_result = await db.execute(
        select(func.avg(Attempt.latency_ms))
        .where(
            and_(
                Attempt.started_at >= one_day_ago,
                Attempt.latency_ms.isnot(None),
            )
        )
    )
    avg_latency_24h = avg_latency_result.scalar()
    
    # Per-schedule metrics
    schedules_result = await db.execute(
        select(Schedule)
        .where(Schedule.status.in_([ScheduleStatus.ACTIVE, ScheduleStatus.PAUSED]))
    )
    schedules = schedules_result.scalars().all()
    
    schedule_metrics = []
    for schedule in schedules:
        # Get run counts by status
        run_stats_result = await db.execute(
            select(Run.status, func.count(Run.id))
            .where(Run.schedule_id == schedule.id)
            .group_by(Run.status)
        )
        run_stats = dict(run_stats_result.fetchall())
        
        total = sum(run_stats.values())
        successful = run_stats.get(RunStatus.SUCCESS, 0)
        failed = run_stats.get(RunStatus.FAILED, 0)
        timeout = run_stats.get(RunStatus.TIMEOUT, 0)
        
        # Average latency for this schedule
        schedule_latency_result = await db.execute(
            select(func.avg(Attempt.latency_ms))
            .join(Run)
            .where(
                and_(
                    Run.schedule_id == schedule.id,
                    Attempt.latency_ms.isnot(None),
                )
            )
        )
        avg_latency = schedule_latency_result.scalar()
        
        schedule_metrics.append(ScheduleMetrics(
            schedule_id=schedule.id,
            schedule_name=schedule.name,
            total_runs=total,
            successful_runs=successful,
            failed_runs=failed,
            timeout_runs=timeout,
            success_rate=(successful / total * 100) if total > 0 else 0.0,
            avg_latency_ms=avg_latency,
            last_run_at=schedule.last_run_at,
        ))
    
    return GlobalMetrics(
        total_targets=total_targets,
        total_schedules=total_schedules,
        active_schedules=active_schedules,
        paused_schedules=paused_schedules,
        total_runs=total_runs,
        runs_last_hour=runs_last_hour,
        runs_last_24h=runs_last_24h,
        success_rate_24h=round(success_rate_24h, 2),
        avg_latency_24h_ms=round(avg_latency_24h, 2) if avg_latency_24h else None,
        schedules=schedule_metrics,
    )


@router.get("/metrics/prometheus")
async def get_prometheus_metrics(
    db: AsyncSession = Depends(get_db),
):
    """
    Get metrics in Prometheus format.
    
    Exposes the following metrics:
    - api_scheduler_targets_total
    - api_scheduler_schedules_total (by status)
    - api_scheduler_runs_total (by status)
    - api_scheduler_run_latency_ms
    - api_scheduler_errors_total (by error type)
    """
    now = now_ist()
    one_hour_ago = now - timedelta(hours=1)
    
    lines = []
    lines.append("# HELP api_scheduler_targets_total Total number of targets")
    lines.append("# TYPE api_scheduler_targets_total gauge")
    
    target_count = await db.execute(select(func.count(Target.id)))
    lines.append(f"api_scheduler_targets_total {target_count.scalar() or 0}")
    
    lines.append("")
    lines.append("# HELP api_scheduler_schedules_total Total number of schedules by status")
    lines.append("# TYPE api_scheduler_schedules_total gauge")
    
    schedule_counts = await db.execute(
        select(Schedule.status, func.count(Schedule.id))
        .group_by(Schedule.status)
    )
    for status, count in schedule_counts.fetchall():
        lines.append(f'api_scheduler_schedules_total{{status="{status.value}"}} {count}')
    
    lines.append("")
    lines.append("# HELP api_scheduler_runs_total Total number of runs by status")
    lines.append("# TYPE api_scheduler_runs_total counter")
    
    run_counts = await db.execute(
        select(Run.status, func.count(Run.id))
        .group_by(Run.status)
    )
    for status, count in run_counts.fetchall():
        lines.append(f'api_scheduler_runs_total{{status="{status.value}"}} {count}')
    
    lines.append("")
    lines.append("# HELP api_scheduler_runs_last_hour Runs in the last hour by status")
    lines.append("# TYPE api_scheduler_runs_last_hour gauge")
    
    recent_runs = await db.execute(
        select(Run.status, func.count(Run.id))
        .where(Run.scheduled_at >= one_hour_ago)
        .group_by(Run.status)
    )
    for status, count in recent_runs.fetchall():
        lines.append(f'api_scheduler_runs_last_hour{{status="{status.value}"}} {count}')
    
    lines.append("")
    lines.append("# HELP api_scheduler_latency_ms Average request latency in milliseconds")
    lines.append("# TYPE api_scheduler_latency_ms gauge")
    
    avg_latency = await db.execute(
        select(func.avg(Attempt.latency_ms))
        .where(Attempt.latency_ms.isnot(None))
    )
    latency_value = avg_latency.scalar()
    lines.append(f"api_scheduler_latency_ms {round(latency_value, 2) if latency_value else 0}")
    
    lines.append("")
    lines.append("# HELP api_scheduler_errors_total Total errors by type")
    lines.append("# TYPE api_scheduler_errors_total counter")
    
    error_counts = await db.execute(
        select(Attempt.error_type, func.count(Attempt.id))
        .where(Attempt.error_type != ErrorType.NONE)
        .group_by(Attempt.error_type)
    )
    for error_type, count in error_counts.fetchall():
        lines.append(f'api_scheduler_errors_total{{type="{error_type.value}"}} {count}')
    
    lines.append("")
    
    return Response(
        content="\n".join(lines),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": now_ist().isoformat(),
    }
