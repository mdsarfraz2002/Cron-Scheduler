"""API routes for managing targets."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Target, Schedule, ScheduleStatus
from app.schemas import TargetCreate, TargetUpdate, TargetResponse, MessageResponse
from app.scheduler import scheduler_engine

router = APIRouter(prefix="/targets", tags=["Targets"])


@router.post("", response_model=TargetResponse, status_code=status.HTTP_201_CREATED)
async def create_target(
    target_data: TargetCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new target.
    
    A target represents an HTTP endpoint to send requests to.
    """
    target = Target(
        name=target_data.name,
        url=target_data.url,
        method=target_data.method,
        headers=target_data.headers,
        body_template=target_data.body_template,
        timeout_seconds=target_data.timeout_seconds,
    )
    db.add(target)
    await db.flush()
    await db.refresh(target)
    
    return target


@router.get("", response_model=List[TargetResponse])
async def list_targets(
    db: AsyncSession = Depends(get_db),
):
    """List all targets."""
    result = await db.execute(select(Target).order_by(Target.created_at.desc()))
    targets = result.scalars().all()
    return targets


@router.get("/{target_id}", response_model=TargetResponse)
async def get_target(
    target_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific target by ID."""
    result = await db.execute(select(Target).where(Target.id == target_id))
    target = result.scalar_one_or_none()
    
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target {target_id} not found"
        )
    
    return target


@router.patch("/{target_id}", response_model=TargetResponse)
async def update_target(
    target_id: str,
    target_data: TargetUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a target."""
    result = await db.execute(select(Target).where(Target.id == target_id))
    target = result.scalar_one_or_none()
    
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target {target_id} not found"
        )
    
    # Update only provided fields
    update_data = target_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(target, field, value)
    
    db.add(target)
    await db.flush()
    await db.refresh(target)
    
    return target


@router.delete("/{target_id}", response_model=MessageResponse)
async def delete_target(
    target_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a target.
    
    This will also delete all associated schedules and runs.
    APScheduler jobs are properly cleaned up.
    """
    result = await db.execute(
        select(Target)
        .where(Target.id == target_id)
        .options(selectinload(Target.schedules))
    )
    target = result.scalar_one_or_none()
    
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target {target_id} not found"
        )
    
    # Remove all associated schedule jobs from APScheduler before cascade delete
    for schedule in target.schedules:
        if schedule.status == ScheduleStatus.ACTIVE:
            scheduler_engine._remove_job_for_schedule(schedule.id)
    
    await db.delete(target)
    
    return MessageResponse(message=f"Target {target_id} deleted successfully")
