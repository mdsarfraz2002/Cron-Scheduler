"""Tests for the API Scheduler."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.models import Base, Target, Schedule, Run, HttpMethod, ScheduleType, ScheduleStatus, RunStatus
from app.database import get_db


# Test database setup
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def override_get_db():
    """Override database dependency for tests."""
    async with TestingSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest.fixture
async def setup_db():
    """Set up test database."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def client(setup_db):
    """Create test client."""
    app.dependency_overrides[get_db] = override_get_db
    return AsyncClient(app=app, base_url="http://test")


# ============= Target Tests =============

@pytest.mark.asyncio
async def test_create_target(client):
    """Test creating a target."""
    async with client as ac:
        response = await ac.post(
            "/api/v1/targets",
            json={
                "name": "Test Target",
                "url": "https://example.com/api",
                "method": "POST",
                "headers": {"Authorization": "Bearer token"},
                "body_template": '{"key": "value"}',
                "timeout_seconds": 15.0,
            }
        )
    
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Target"
    assert data["url"] == "https://example.com/api"
    assert data["method"] == "POST"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_target_invalid_url(client):
    """Test that invalid URLs are rejected."""
    async with client as ac:
        response = await ac.post(
            "/api/v1/targets",
            json={
                "name": "Invalid Target",
                "url": "not-a-valid-url",
                "method": "GET",
            }
        )
    
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_targets(client):
    """Test listing targets."""
    async with client as ac:
        # Create two targets
        await ac.post("/api/v1/targets", json={
            "name": "Target 1",
            "url": "https://example.com/1",
        })
        await ac.post("/api/v1/targets", json={
            "name": "Target 2",
            "url": "https://example.com/2",
        })
        
        response = await ac.get("/api/v1/targets")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_get_target(client):
    """Test getting a specific target."""
    async with client as ac:
        create_response = await ac.post("/api/v1/targets", json={
            "name": "Get Me",
            "url": "https://example.com/get",
        })
        target_id = create_response.json()["id"]
        
        response = await ac.get(f"/api/v1/targets/{target_id}")
    
    assert response.status_code == 200
    assert response.json()["name"] == "Get Me"


@pytest.mark.asyncio
async def test_get_target_not_found(client):
    """Test getting a non-existent target."""
    async with client as ac:
        response = await ac.get("/api/v1/targets/nonexistent-id")
    
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_target(client):
    """Test updating a target."""
    async with client as ac:
        create_response = await ac.post("/api/v1/targets", json={
            "name": "Original Name",
            "url": "https://example.com/original",
        })
        target_id = create_response.json()["id"]
        
        response = await ac.patch(
            f"/api/v1/targets/{target_id}",
            json={"name": "Updated Name"}
        )
    
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"
    assert response.json()["url"] == "https://example.com/original"


@pytest.mark.asyncio
async def test_delete_target(client):
    """Test deleting a target."""
    async with client as ac:
        create_response = await ac.post("/api/v1/targets", json={
            "name": "Delete Me",
            "url": "https://example.com/delete",
        })
        target_id = create_response.json()["id"]
        
        delete_response = await ac.delete(f"/api/v1/targets/{target_id}")
        assert delete_response.status_code == 200
        
        get_response = await ac.get(f"/api/v1/targets/{target_id}")
        assert get_response.status_code == 404


# ============= Schedule Tests =============

@pytest.mark.asyncio
async def test_create_interval_schedule(client):
    """Test creating an interval schedule."""
    async with client as ac:
        # Create target first
        target_response = await ac.post("/api/v1/targets", json={
            "name": "Schedule Target",
            "url": "https://example.com/api",
        })
        target_id = target_response.json()["id"]
        
        response = await ac.post("/api/v1/schedules", json={
            "name": "Every 30 seconds",
            "target_id": target_id,
            "schedule_type": "interval",
            "interval_seconds": 30,
            "duration_seconds": 300,
        })
    
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Every 30 seconds"
    assert data["interval_seconds"] == 30
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_create_cron_schedule(client):
    """Test creating a cron schedule."""
    async with client as ac:
        target_response = await ac.post("/api/v1/targets", json={
            "name": "Cron Target",
            "url": "https://example.com/api",
        })
        target_id = target_response.json()["id"]
        
        response = await ac.post("/api/v1/schedules", json={
            "name": "Every 5 minutes",
            "target_id": target_id,
            "schedule_type": "cron",
            "cron_expression": "*/5 * * * *",
            "max_runs": 10,
        })
    
    assert response.status_code == 201
    data = response.json()
    assert data["cron_expression"] == "*/5 * * * *"


@pytest.mark.asyncio
async def test_create_schedule_invalid_target(client):
    """Test that schedules require valid targets."""
    async with client as ac:
        response = await ac.post("/api/v1/schedules", json={
            "name": "Invalid Schedule",
            "target_id": "nonexistent-target",
            "schedule_type": "interval",
            "interval_seconds": 30,
        })
    
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_pause_resume_schedule(client):
    """Test pausing and resuming a schedule."""
    async with client as ac:
        # Create target and schedule
        target_response = await ac.post("/api/v1/targets", json={
            "name": "Pause Target",
            "url": "https://example.com/api",
        })
        target_id = target_response.json()["id"]
        
        schedule_response = await ac.post("/api/v1/schedules", json={
            "name": "Pausable Schedule",
            "target_id": target_id,
            "schedule_type": "interval",
            "interval_seconds": 60,
        })
        schedule_id = schedule_response.json()["id"]
        
        # Pause
        pause_response = await ac.post(f"/api/v1/schedules/{schedule_id}/pause")
        assert pause_response.status_code == 200
        assert pause_response.json()["status"] == "paused"
        
        # Resume
        resume_response = await ac.post(f"/api/v1/schedules/{schedule_id}/resume")
        assert resume_response.status_code == 200
        assert resume_response.json()["status"] == "active"


# ============= Run Tests =============

@pytest.mark.asyncio
async def test_list_runs_empty(client):
    """Test listing runs when none exist."""
    async with client as ac:
        response = await ac.get("/api/v1/runs")
    
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio  
async def test_get_run_not_found(client):
    """Test getting a non-existent run."""
    async with client as ac:
        response = await ac.get("/api/v1/runs/nonexistent-id")
    
    assert response.status_code == 404


# ============= Metrics Tests =============

@pytest.mark.asyncio
async def test_get_metrics(client):
    """Test getting metrics."""
    async with client as ac:
        response = await ac.get("/api/v1/metrics")
    
    assert response.status_code == 200
    data = response.json()
    assert "total_targets" in data
    assert "total_schedules" in data
    assert "total_runs" in data


@pytest.mark.asyncio
async def test_get_prometheus_metrics(client):
    """Test getting Prometheus-format metrics."""
    async with client as ac:
        response = await ac.get("/api/v1/metrics/prometheus")
    
    assert response.status_code == 200
    assert "api_scheduler_targets_total" in response.text


@pytest.mark.asyncio
async def test_health_check(client):
    """Test health check endpoint."""
    async with client as ac:
        response = await ac.get("/api/v1/health")
    
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


# ============= Integration Tests =============

@pytest.mark.asyncio
async def test_full_workflow(client):
    """Test the complete workflow: target -> schedule -> check runs."""
    async with client as ac:
        # 1. Create target
        target_response = await ac.post("/api/v1/targets", json={
            "name": "Integration Test Target",
            "url": "https://httpbin.org/post",
            "method": "POST",
            "headers": {"Content-Type": "application/json"},
            "body_template": '{"test": true}',
        })
        assert target_response.status_code == 201
        target_id = target_response.json()["id"]
        
        # 2. Create schedule
        schedule_response = await ac.post("/api/v1/schedules", json={
            "name": "Integration Test Schedule",
            "target_id": target_id,
            "schedule_type": "interval",
            "interval_seconds": 60,
            "max_runs": 5,
        })
        assert schedule_response.status_code == 201
        schedule_id = schedule_response.json()["id"]
        
        # 3. Check schedule is active
        get_schedule = await ac.get(f"/api/v1/schedules/{schedule_id}")
        assert get_schedule.json()["status"] == "active"
        
        # 4. Pause schedule
        pause_response = await ac.post(f"/api/v1/schedules/{schedule_id}/pause")
        assert pause_response.json()["status"] == "paused"
        
        # 5. Check metrics
        metrics_response = await ac.get("/api/v1/metrics")
        assert metrics_response.json()["total_targets"] >= 1
        assert metrics_response.json()["paused_schedules"] >= 1
        
        # 6. Delete schedule
        delete_response = await ac.delete(f"/api/v1/schedules/{schedule_id}")
        assert delete_response.status_code == 200
        
        # 7. Delete target
        delete_target = await ac.delete(f"/api/v1/targets/{target_id}")
        assert delete_target.status_code == 200
