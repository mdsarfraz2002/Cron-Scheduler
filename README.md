# API Scheduler

> **Cron for API Calls** - A backend service that lets users schedule HTTP requests to external targets.

## Features

- **Targets**: Define HTTP endpoints with URL, method, headers, and optional body templates
- **Flexible Scheduling**: Support for interval-based and cron expression scheduling
- **Window Constraints**: Limit schedules by duration or maximum run count
- **Automatic Retries**: Configurable retry logic with error classification
- **Detailed Tracking**: Full request/response metadata for every attempt
- **Observability**: JSON metrics and Prometheus-format endpoints
- **Graceful Recovery**: Schedules survive server restarts without duplication

## Quick Start

### Prerequisites

- Python 3.10+
- pip or uv package manager

### Installation

```bash
# Clone the repository
cd interview

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Running the Server

```bash
# Start the server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Or using Python directly
python -m app.main
```

The API will be available at `http://localhost:8000`

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/api/v1/health

## API Usage

### 1. Create a Target

```bash
curl -X POST http://localhost:8000/api/v1/targets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Example API",
    "url": "https://httpbin.org/post",
    "method": "POST",
    "headers": {"Content-Type": "application/json"},
    "body_template": "{\"message\": \"Hello from scheduler\"}",
    "timeout_seconds": 30
  }'
```

### 2. Create a Schedule

**Interval-based (every 10 seconds for 5 minutes):**

```bash
curl -X POST http://localhost:8000/api/v1/schedules \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Every 10 seconds",
    "target_id": "<TARGET_ID>",
    "schedule_type": "interval",
    "interval_seconds": 10,
    "duration_seconds": 300
  }'
```

**Cron-based (every 5 minutes):**

```bash
curl -X POST http://localhost:8000/api/v1/schedules \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Every 5 minutes",
    "target_id": "<TARGET_ID>",
    "schedule_type": "cron",
    "cron_expression": "*/5 * * * *",
    "max_runs": 100
  }'
```

### 3. Monitor Runs

```bash
# List all runs
curl http://localhost:8000/api/v1/runs

# Filter by schedule
curl "http://localhost:8000/api/v1/runs?schedule_id=<SCHEDULE_ID>"

# Filter by status
curl "http://localhost:8000/api/v1/runs?status=failed"

# Get run details with attempts
curl http://localhost:8000/api/v1/runs/<RUN_ID>
```

### 4. Control Schedules

```bash
# Pause a schedule
curl -X POST http://localhost:8000/api/v1/schedules/<SCHEDULE_ID>/pause

# Resume a schedule
curl -X POST http://localhost:8000/api/v1/schedules/<SCHEDULE_ID>/resume

# Delete a schedule
curl -X DELETE http://localhost:8000/api/v1/schedules/<SCHEDULE_ID>
```

### 5. View Metrics

```bash
# JSON metrics
curl http://localhost:8000/api/v1/metrics

# Prometheus format
curl http://localhost:8000/api/v1/metrics/prometheus
```

## API Reference

| Endpoint                          | Method | Description              |
| --------------------------------- | ------ | ------------------------ |
| `/api/v1/targets`               | POST   | Create a new target      |
| `/api/v1/targets`               | GET    | List all targets         |
| `/api/v1/targets/{id}`          | GET    | Get target details       |
| `/api/v1/targets/{id}`          | PATCH  | Update a target          |
| `/api/v1/targets/{id}`          | DELETE | Delete a target          |
| `/api/v1/schedules`             | POST   | Create a new schedule    |
| `/api/v1/schedules`             | GET    | List all schedules       |
| `/api/v1/schedules/{id}`        | GET    | Get schedule details     |
| `/api/v1/schedules/{id}`        | PATCH  | Update a schedule        |
| `/api/v1/schedules/{id}/pause`  | POST   | Pause a schedule         |
| `/api/v1/schedules/{id}/resume` | POST   | Resume a schedule        |
| `/api/v1/schedules/{id}`        | DELETE | Delete a schedule        |
| `/api/v1/runs`                  | GET    | List runs (with filters) |
| `/api/v1/runs/{id}`             | GET    | Get run with attempts    |
| `/api/v1/metrics`               | GET    | JSON metrics             |
| `/api/v1/metrics/prometheus`    | GET    | Prometheus metrics       |
| `/api/v1/health`                | GET    | Health check             |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FastAPI App                              │
├─────────────────────────────────────────────────────────────────┤
│  Routers: targets.py | schedules.py | runs.py | metrics.py      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────┐    ┌──────────────────┐                    │
│  │ Scheduler Engine │◄──►│  HTTP Executor   │                    │
│  │  (APScheduler)   │    │  (httpx async)   │                    │
│  └────────┬────────┘    └────────┬─────────┘                    │
│           │                      │                               │
│           ▼                      ▼                               │
│  ┌─────────────────────────────────────────┐                    │
│  │         SQLAlchemy Async ORM            │                    │
│  └─────────────────────────────────────────┘                    │
│           │                                                      │
│           ▼                                                      │
│  ┌─────────────────────────────────────────┐                    │
│  │     SQLite / PostgreSQL Database        │                    │
│  └─────────────────────────────────────────┘                    │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

1. **Scheduler Engine** (`app/scheduler.py`)

   - Built on APScheduler for reliable job management
   - Recovers active schedules on startup
   - Prevents duplicate runs with locking
   - Handles window expiration automatically
2. **HTTP Executor** (`app/executor.py`)

   - Async HTTP client with httpx
   - Automatic retries with configurable limits
   - Error classification (timeout, DNS, connection, SSL, 4xx, 5xx)
   - Detailed attempt tracking
3. **Data Models** (`app/models.py`)

   - Target: HTTP endpoint definition
   - Schedule: Timing configuration
   - Run: Single execution instance
   - Attempt: Individual HTTP request attempt

## Edge Cases & Gotchas Handled

This implementation addresses several non-obvious edge cases:

### 1. Orphaned Runs After Server Crash

**Problem**: If the server crashes while a run is in progress, it stays stuck in `RUNNING` status forever.

**Solution**: On startup, all runs in `RUNNING` or `PENDING` status are marked as `FAILED` with an explanatory message.

### 2. Duplicate Run Prevention

**Problem**: Race conditions could cause the same schedule to fire twice, creating duplicate runs.

**Solution**: Each run has an `idempotency_key` (schedule_id + timestamp bucket) with a unique constraint. Duplicate attempts are detected and skipped.

### 3. Cascade Delete Cleanup

**Problem**: When a target is deleted, the database cascade deletes schedules, but APScheduler jobs remain orphaned.

**Solution**: The target delete endpoint explicitly removes all associated schedule jobs from APScheduler before the database delete.

### 4. Time Zone Consistency

**Problem**: Mixing `datetime.utcnow()` with APScheduler's local time handling causes scheduling misalignment.

**Solution**: All timestamps use a consistent `now_ist()` helper that returns naive IST (Asia/Kolkata, UTC+5:30) datetimes using `pytz`. APScheduler is configured to work in IST.

### 5. Large Response Handling

**Problem**: Storing huge response bodies could cause memory exhaustion (OOM).

**Solution**: Response bodies are truncated at 100KB with a clear indication when truncation occurs.

### 6. Exponential Backoff

**Problem**: Fixed retry delays can hammer failing targets unnecessarily.

**Solution**: Retries use exponential backoff: 1s → 2s → 4s (with configurable base and max).

### 7. Validation Edge Cases

**Problem**: Creating an interval schedule without `interval_seconds` would cause runtime errors.

**Solution**: Pydantic model validators ensure required fields are present based on schedule type, returning clear 422 errors.

### 8. SSL Certificate Errors

**Problem**: SSL errors were being classified as generic connection errors.

**Solution**: Enhanced error classification specifically detects SSL/TLS certificate issues.

## Design Decisions & Tradeoffs

### 1. APScheduler for Job Management

**Decision**: Use APScheduler with in-memory job store.

**Why**:

- Mature, battle-tested library
- Built-in coalescing prevents missed job pile-up
- `max_instances=1` prevents overlapping runs
- Simple recovery from database on restart

**Tradeoff**: Jobs are stored in memory, so the scheduler state is rebuilt from the database on restart. This adds startup time but ensures single source of truth.

### 2. SQLite by Default, PostgreSQL Ready

**Decision**: Use SQLite for development, easy switch to PostgreSQL.

**Why**:

- Zero configuration for local development
- Same SQLAlchemy code works with PostgreSQL
- Async support via aiosqlite/asyncpg

**Tradeoff**: SQLite doesn't support true concurrent writes. For production with high concurrency, PostgreSQL is recommended.

### 3. Retry Strategy

**Decision**: Retry with exponential backoff; don't retry client errors (4xx).

**Why**:

- 4xx errors indicate client mistakes (won't self-heal)
- 5xx and network errors are often transient
- Exponential backoff prevents hammering failing targets
- Configurable retry count and base delay

### 4. Run/Attempt Separation

**Decision**: Separate Run and Attempt tables.

**Why**:

- Run = "we intended to execute at this time"
- Attempt = "we actually tried N times"
- Enables retry tracking and detailed debugging
- Clear audit trail

### 5. Window-Based Scheduling

**Decision**: Support both duration-based and max-runs-based windows.

**Why**:

- "Run for 5 minutes" vs "Run 10 times" are both valid use cases
- Easy to implement with expiration tracking
- Automatic cleanup when window ends

## What I Would Do Next for Production

### Immediate Priorities

1. **Switch to PostgreSQL**: Better concurrency, ACID guarantees, connection pooling
2. **Add Authentication**: JWT-based auth, API keys, rate limiting
3. **Distributed Job Store**: Redis or PostgreSQL for APScheduler jobs to support multiple worker nodes
4. **Job Queuing**: Add Celery or similar for reliable background execution

### Observability

5. **Structured Logging**: JSON logs with correlation IDs
6. **OpenTelemetry**: Distributed tracing for debugging
7. **Alerting**: PagerDuty/Slack integration for failed schedules

### Reliability

8. **Dead Letter Queue**: Capture permanently failed runs for investigation
9. **Circuit Breaker**: Stop hammering failing targets
10. **Webhook Notifications**: Notify on schedule completion/failure

### Scalability

11. **Horizontal Scaling**: Multiple worker processes with shared job store
12. **Database Partitioning**: Partition runs table by date for performance
13. **Archival**: Move old runs to cold storage

### Security

14. **Secrets Management**: Vault integration for target credentials
15. **Request Signing**: HMAC signatures for outbound requests
16. **Audit Logging**: Track who changed what

## Configuration

Environment variables (or `.env` file):

| Variable                      | Default                                | Description                  |
| ----------------------------- | -------------------------------------- | ---------------------------- |
| `DATABASE_URL`              | `sqlite+aiosqlite:///./scheduler.db` | Database connection string   |
| `DEFAULT_TIMEOUT_SECONDS`   | `30`                                 | Default HTTP timeout         |
| `MAX_TIMEOUT_SECONDS`       | `120`                                | Maximum allowed timeout      |
| `MAX_RETRIES`               | `3`                                  | Number of retry attempts     |
| `RETRY_DELAY_SECONDS`       | `1`                                  | Delay between retries        |
| `MAX_CONCURRENT_JOBS`       | `100`                                | Max simultaneous jobs        |
| `JOB_MISFIRE_GRACE_SECONDS` | `60`                                 | Grace period for missed jobs |
| `DEBUG`                     | `false`                              | Enable debug mode            |

## Testing

```bash
# Run tests
pytest

# With coverage
pytest --cov=app --cov-report=html
```
