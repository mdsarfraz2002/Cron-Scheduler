"""API routes for managing schedules."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Schedule, Target, ScheduleStatus
from app.schemas import ScheduleCreate, ScheduleUpdate, ScheduleResponse, MessageResponse
from app.scheduler import scheduler_engine

router = APIRouter(prefix="/schedules", tags=["Schedules"])


@router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    schedule_data: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new schedule.
    
    A schedule defines when to send requests to a target.
    
    - **interval**: Run every `interval_seconds` seconds
    - **cron**: Run according to cron expression
    - **duration_seconds**: Optional window - stop after this many seconds
    - **max_runs**: Optional limit - stop after this many runs
    """
    # Verify target exists
    result = await db.execute(select(Target).where(Target.id == schedule_data.target_id))
    target = result.scalar_one_or_none()
    
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target {schedule_data.target_id} not found"
        )
    
    schedule = Schedule(
        name=schedule_data.name,
        target_id=schedule_data.target_id,
        schedule_type=schedule_data.schedule_type,
        interval_seconds=schedule_data.interval_seconds,
        cron_expression=schedule_data.cron_expression,
        duration_seconds=schedule_data.duration_seconds,
        max_runs=schedule_data.max_runs,
        status=ScheduleStatus.ACTIVE,
    )
    
    db.add(schedule)
    await db.flush()
    
    # Load target relationship
    schedule.target = target
    
    # Add to scheduler engine
    await scheduler_engine.add_schedule(schedule)
    
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    
    return schedule


@router.get("", response_model=List[ScheduleResponse])
async def list_schedules(
    status_filter: ScheduleStatus = None,
    db: AsyncSession = Depends(get_db),
):
    """List all schedules, optionally filtered by status."""
    query = select(Schedule).order_by(Schedule.created_at.desc())
    
    if status_filter:
        query = query.where(Schedule.status == status_filter)
    
    result = await db.execute(query)
    schedules = result.scalars().all()
    
    return schedules


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific schedule by ID."""
    result = await db.execute(
        select(Schedule).where(Schedule.id == schedule_id)
    )
    schedule = result.scalar_one_or_none()
    
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found"
        )
    
    return schedule


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str,
    schedule_data: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a schedule."""
    result = await db.execute(
        select(Schedule)
        .where(Schedule.id == schedule_id)
        .options(selectinload(Schedule.target))
    )
    schedule = result.scalar_one_or_none()
    
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found"
        )
    
    # Update only provided fields
    update_data = schedule_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(schedule, field, value)
    
    # If schedule is active, update the job
    if schedule.status == ScheduleStatus.ACTIVE:
        await scheduler_engine.add_schedule(schedule)
    
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    
    return schedule


@router.post("/{schedule_id}/pause", response_model=ScheduleResponse)
async def pause_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Pause an active schedule."""
    result = await db.execute(
        select(Schedule).where(Schedule.id == schedule_id)
    )
    schedule = result.scalar_one_or_none()
    
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found"
        )
    
    if schedule.status != ScheduleStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot pause schedule with status {schedule.status}"
        )
    
    await scheduler_engine.pause_schedule(schedule)
    
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    
    return schedule


@router.post("/{schedule_id}/resume", response_model=ScheduleResponse)
async def resume_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Resume a paused schedule."""
    result = await db.execute(
        select(Schedule)
        .where(Schedule.id == schedule_id)
        .options(selectinload(Schedule.target))
    )
    schedule = result.scalar_one_or_none()
    
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found"
        )
    
    if schedule.status != ScheduleStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot resume schedule with status {schedule.status}"
        )
    
    await scheduler_engine.resume_schedule(schedule)
    
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    
    return schedule


@router.delete("/{schedule_id}", response_model=MessageResponse)
async def delete_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a schedule.
    
    This will also delete all associated runs and attempts.
    """
    result = await db.execute(
        select(Schedule).where(Schedule.id == schedule_id)
    )
    schedule = result.scalar_one_or_none()
    
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found"
        )
    
    await scheduler_engine.delete_schedule(schedule)
    await db.delete(schedule)
    
    return MessageResponse(message=f"Schedule {schedule_id} deleted successfully")
