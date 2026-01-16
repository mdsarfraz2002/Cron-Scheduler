"""API routes for viewing runs and attempts."""
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Run, Attempt, RunStatus
from app.schemas import RunResponse, RunDetailResponse, AttemptResponse

router = APIRouter(prefix="/runs", tags=["Runs"])


@router.get("", response_model=List[RunResponse])
async def list_runs(
    schedule_id: Optional[str] = Query(None, description="Filter by schedule ID"),
    status_filter: Optional[RunStatus] = Query(None, alias="status", description="Filter by status"),
    start_time: Optional[datetime] = Query(None, description="Filter runs scheduled after this time"),
    end_time: Optional[datetime] = Query(None, description="Filter runs scheduled before this time"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of runs to return"),
    offset: int = Query(0, ge=0, description="Number of runs to skip"),
    db: AsyncSession = Depends(get_db),
):
    """
    List runs with optional filtering.
    
    Filters:
    - **schedule_id**: Filter by schedule
    - **status**: Filter by run status (pending, running, success, failed, timeout)
    - **start_time**: Filter runs scheduled after this time
    - **end_time**: Filter runs scheduled before this time
    """
    query = select(Run).order_by(Run.scheduled_at.desc())
    
    conditions = []
    
    if schedule_id:
        conditions.append(Run.schedule_id == schedule_id)
    
    if status_filter:
        conditions.append(Run.status == status_filter)
    
    if start_time:
        conditions.append(Run.scheduled_at >= start_time)
    
    if end_time:
        conditions.append(Run.scheduled_at <= end_time)
    
    if conditions:
        query = query.where(and_(*conditions))
    
    query = query.limit(limit).offset(offset)
    
    result = await db.execute(query)
    runs = result.scalars().all()
    
    return runs


@router.get("/count")
async def count_runs(
    schedule_id: Optional[str] = Query(None, description="Filter by schedule ID"),
    status_filter: Optional[RunStatus] = Query(None, alias="status", description="Filter by status"),
    start_time: Optional[datetime] = Query(None, description="Filter runs scheduled after this time"),
    end_time: Optional[datetime] = Query(None, description="Filter runs scheduled before this time"),
    db: AsyncSession = Depends(get_db),
):
    """Get count of runs matching filters."""
    query = select(func.count(Run.id))
    
    conditions = []
    
    if schedule_id:
        conditions.append(Run.schedule_id == schedule_id)
    
    if status_filter:
        conditions.append(Run.status == status_filter)
    
    if start_time:
        conditions.append(Run.scheduled_at >= start_time)
    
    if end_time:
        conditions.append(Run.scheduled_at <= end_time)
    
    if conditions:
        query = query.where(and_(*conditions))
    
    result = await db.execute(query)
    count = result.scalar()
    
    return {"count": count}


@router.get("/{run_id}", response_model=RunDetailResponse)
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed information about a specific run.
    
    Includes all attempts with full request/response details.
    """
    result = await db.execute(
        select(Run)
        .where(Run.id == run_id)
        .options(selectinload(Run.attempts))
    )
    run = result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found"
        )
    
    # Sort attempts by attempt number
    run.attempts.sort(key=lambda a: a.attempt_number)
    
    return run


@router.get("/{run_id}/attempts", response_model=List[AttemptResponse])
async def get_run_attempts(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all attempts for a specific run."""
    # Verify run exists
    run_result = await db.execute(select(Run).where(Run.id == run_id))
    run = run_result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found"
        )
    
    result = await db.execute(
        select(Attempt)
        .where(Attempt.run_id == run_id)
        .order_by(Attempt.attempt_number)
    )
    attempts = result.scalars().all()
    
    return attempts
