"""Scheduler engine using APScheduler for reliable job management."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Set
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
import pytz

from app.models import Schedule, Target, Run, ScheduleStatus, ScheduleType, RunStatus
from app.database import get_session, async_session_factory
from app.executor import executor
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# IST timezone
IST = pytz.timezone('Asia/Kolkata')


def now_ist() -> datetime:
    """Get current IST time as naive datetime for database storage."""
    return datetime.now(IST).replace(tzinfo=None)


class SchedulerEngine:
    """
    Manages job scheduling with APScheduler.
    
    Features:
    - Recovers active schedules on startup
    - Handles window expiration
    - Prevents duplicate runs
    - Thread-safe run tracking
    """
    
    def __init__(self):
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._running_jobs: Set[str] = set()  # Track in-flight runs
        self._lock = asyncio.Lock()
        self._started = False
    
    @property
    def scheduler(self) -> AsyncIOScheduler:
        """Get or create scheduler instance."""
        if self._scheduler is None:
            jobstores = {
                'default': MemoryJobStore()
            }
            executors_config = {
                'default': AsyncIOExecutor()
            }
            job_defaults = {
                'coalesce': True,  # Combine missed runs into one
                'max_instances': 1,  # Prevent overlapping runs
                'misfire_grace_time': settings.job_misfire_grace_seconds,
            }
            
            self._scheduler = AsyncIOScheduler(
                jobstores=jobstores,
                executors=executors_config,
                job_defaults=job_defaults,
            )
        return self._scheduler
    
    async def start(self):
        """Start the scheduler and recover active schedules."""
        if self._started:
            return
        
        logger.info("Starting scheduler engine...")
        
        # Start APScheduler
        self.scheduler.start()
        self._started = True
        
        # Recover active schedules from database
        await self._recover_schedules()
        
        # Start window expiration checker
        self.scheduler.add_job(
            self._check_expired_windows,
            IntervalTrigger(seconds=60),
            id='__window_expiration_checker__',
            replace_existing=True,
        )
        
        logger.info("Scheduler engine started successfully")
    
    async def shutdown(self):
        """Gracefully shutdown the scheduler."""
        if not self._started:
            return
        
        logger.info("Shutting down scheduler engine...")
        
        if self._scheduler:
            self._scheduler.shutdown(wait=True)
        
        await executor.close()
        self._started = False
        
        logger.info("Scheduler engine shutdown complete")
    
    async def _recover_schedules(self):
        """Recover active schedules from the database after restart."""
        async with async_session_factory() as session:
            # First, recover orphaned runs that were stuck in RUNNING/PENDING state
            await self._recover_orphaned_runs(session)
            
            result = await session.execute(
                select(Schedule)
                .where(Schedule.status == ScheduleStatus.ACTIVE)
                .options(selectinload(Schedule.target))
            )
            schedules = result.scalars().all()
            
            recovered_count = 0
            for schedule in schedules:
                try:
                    # Check if window has expired
                    if schedule.expires_at and now_ist() >= schedule.expires_at:
                        schedule.status = ScheduleStatus.EXPIRED
                        session.add(schedule)
                        continue
                    
                    # Check if max runs reached
                    if schedule.max_runs and schedule.run_count >= schedule.max_runs:
                        schedule.status = ScheduleStatus.EXPIRED
                        session.add(schedule)
                        continue
                    
                    # Re-add to scheduler
                    self._add_job_for_schedule(schedule)
                    recovered_count += 1
                    
                except Exception as e:
                    logger.error(f"Failed to recover schedule {schedule.id}: {e}")
            
            await session.commit()
            logger.info(f"Recovered {recovered_count} active schedules")
    
    async def _recover_orphaned_runs(self, session):
        """
        Mark runs stuck in RUNNING/PENDING as FAILED.
        
        This handles the case where the server crashed mid-execution.
        """
        orphaned_statuses = [RunStatus.RUNNING, RunStatus.PENDING]
        result = await session.execute(
            select(Run).where(Run.status.in_(orphaned_statuses))
        )
        orphaned_runs = result.scalars().all()
        
        for run in orphaned_runs:
            run.status = RunStatus.FAILED
            run.completed_at = now_ist()
            run.final_error_message = "Server restarted while run was in progress"
            session.add(run)
            logger.warning(f"Marked orphaned run {run.id} as FAILED")
        
        if orphaned_runs:
            await session.flush()
            logger.info(f"Recovered {len(orphaned_runs)} orphaned runs")
    
    def _add_job_for_schedule(self, schedule: Schedule):
        """Add APScheduler job for a schedule."""
        job_id = f"schedule_{schedule.id}"
        
        # Remove existing job if any
        existing = self.scheduler.get_job(job_id)
        if existing:
            self.scheduler.remove_job(job_id)
        
        # Create trigger based on schedule type
        if schedule.schedule_type == ScheduleType.INTERVAL:
            trigger = IntervalTrigger(seconds=schedule.interval_seconds)
        else:
            # Parse cron expression
            parts = schedule.cron_expression.split()
            trigger = CronTrigger(
                minute=parts[0] if len(parts) > 0 else '*',
                hour=parts[1] if len(parts) > 1 else '*',
                day=parts[2] if len(parts) > 2 else '*',
                month=parts[3] if len(parts) > 3 else '*',
                day_of_week=parts[4] if len(parts) > 4 else '*',
            )
        
        # Add job
        self.scheduler.add_job(
            self._execute_schedule,
            trigger,
            id=job_id,
            args=[schedule.id],
            replace_existing=True,
        )
        
        logger.info(f"Added job for schedule {schedule.id} ({schedule.name})")
    
    def _remove_job_for_schedule(self, schedule_id: str):
        """Remove APScheduler job for a schedule."""
        job_id = f"schedule_{schedule_id}"
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed job for schedule {schedule_id}")
        except Exception:
            pass  # Job might not exist
    
    async def add_schedule(self, schedule: Schedule):
        """Add a new schedule."""
        # Set start time and calculate expiration
        if schedule.started_at is None:
            schedule.started_at = now_ist()
        
        if schedule.duration_seconds and schedule.expires_at is None:
            schedule.expires_at = schedule.started_at + timedelta(seconds=schedule.duration_seconds)
        
        # Calculate next run time
        schedule.next_run_at = self._calculate_next_run(schedule)
        
        # Add to APScheduler
        self._add_job_for_schedule(schedule)
    
    async def pause_schedule(self, schedule: Schedule):
        """Pause a schedule."""
        self._remove_job_for_schedule(schedule.id)
        schedule.status = ScheduleStatus.PAUSED
        schedule.next_run_at = None
    
    async def resume_schedule(self, schedule: Schedule):
        """Resume a paused schedule."""
        # Check if still valid
        if schedule.expires_at and datetime.now_ist() >= schedule.expires_at:
            schedule.status = ScheduleStatus.EXPIRED
            return
        
        if schedule.max_runs and schedule.run_count >= schedule.max_runs:
            schedule.status = ScheduleStatus.EXPIRED
            return
        
        schedule.status = ScheduleStatus.ACTIVE
        schedule.next_run_at = self._calculate_next_run(schedule)
        self._add_job_for_schedule(schedule)
    
    async def delete_schedule(self, schedule: Schedule):
        """Delete a schedule."""
        self._remove_job_for_schedule(schedule.id)
        schedule.status = ScheduleStatus.DELETED
    
    def _calculate_next_run(self, schedule: Schedule) -> Optional[datetime]:
        """Calculate the next run time for a schedule."""
        now = now_ist()
        
        if schedule.schedule_type == ScheduleType.INTERVAL:
            return now + timedelta(seconds=schedule.interval_seconds)
        else:
            from croniter import croniter
            cron = croniter(schedule.cron_expression, now)
            return cron.get_next(datetime)
    
    async def _execute_schedule(self, schedule_id: str):
        """Execute a scheduled run. Called by APScheduler."""
        async with self._lock:
            # Prevent duplicate runs
            if schedule_id in self._running_jobs:
                logger.warning(f"Schedule {schedule_id} is already running, skipping")
                return
            self._running_jobs.add(schedule_id)
        
        try:
            async with async_session_factory() as session:
                # Fetch schedule with target
                result = await session.execute(
                    select(Schedule)
                    .where(Schedule.id == schedule_id)
                    .options(selectinload(Schedule.target))
                )
                schedule = result.scalar_one_or_none()
                
                if not schedule:
                    logger.error(f"Schedule {schedule_id} not found")
                    return
                
                if schedule.status != ScheduleStatus.ACTIVE:
                    logger.info(f"Schedule {schedule_id} is not active, skipping")
                    return
                
                # Check window expiration
                if schedule.expires_at and now_ist() >= schedule.expires_at:
                    schedule.status = ScheduleStatus.EXPIRED
                    self._remove_job_for_schedule(schedule_id)
                    session.add(schedule)
                    await session.commit()
                    logger.info(f"Schedule {schedule_id} expired")
                    return
                
                # Check max runs
                if schedule.max_runs and schedule.run_count >= schedule.max_runs:
                    schedule.status = ScheduleStatus.EXPIRED
                    self._remove_job_for_schedule(schedule_id)
                    session.add(schedule)
                    await session.commit()
                    logger.info(f"Schedule {schedule_id} reached max runs")
                    return
                
                # Create run record with idempotency key to prevent duplicates
                now = now_ist()
                # Idempotency key: schedule_id:timestamp_bucket (1-second resolution)
                idempotency_key = f"{schedule.id}:{now.strftime('%Y%m%d%H%M%S')}"
                
                # Check if run already exists with this key (race condition protection)
                existing_run = await session.execute(
                    select(Run).where(Run.idempotency_key == idempotency_key)
                )
                if existing_run.scalar_one_or_none():
                    logger.warning(
                        f"Duplicate run detected for schedule {schedule_id} "
                        f"with key {idempotency_key}, skipping"
                    )
                    return
                
                run = Run(
                    schedule_id=schedule.id,
                    status=RunStatus.PENDING,
                    scheduled_at=now,
                    idempotency_key=idempotency_key,
                )
                session.add(run)
                await session.flush()
                
                # Execute the HTTP request
                target = schedule.target
                run = await executor.execute_run(session, run, target)
                
                # Update schedule stats
                schedule.run_count += 1
                schedule.last_run_at = now_ist()
                schedule.next_run_at = self._calculate_next_run(schedule)
                
                # Check if we've hit max runs
                if schedule.max_runs and schedule.run_count >= schedule.max_runs:
                    schedule.status = ScheduleStatus.EXPIRED
                    self._remove_job_for_schedule(schedule_id)
                
                session.add(schedule)
                await session.commit()
                
                logger.info(
                    f"Schedule {schedule_id} run completed: "
                    f"status={run.status}, run_count={schedule.run_count}"
                )
                
        except Exception as e:
            logger.exception(f"Error executing schedule {schedule_id}: {e}")
        finally:
            async with self._lock:
                self._running_jobs.discard(schedule_id)
    
    async def _check_expired_windows(self):
        """Periodically check and expire schedules whose windows have ended."""
        try:
            async with async_session_factory() as session:
                now = now_ist()
                
                result = await session.execute(
                    select(Schedule)
                    .where(
                        and_(
                            Schedule.status == ScheduleStatus.ACTIVE,
                            Schedule.expires_at.isnot(None),
                            Schedule.expires_at <= now,
                        )
                    )
                )
                expired_schedules = result.scalars().all()
                
                for schedule in expired_schedules:
                    schedule.status = ScheduleStatus.EXPIRED
                    self._remove_job_for_schedule(schedule.id)
                    session.add(schedule)
                    logger.info(f"Schedule {schedule.id} expired due to window")
                
                await session.commit()
                
        except Exception as e:
            logger.exception(f"Error checking expired windows: {e}")


# Global scheduler instance
scheduler_engine = SchedulerEngine()
