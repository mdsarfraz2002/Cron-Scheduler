"""
API Scheduler - Cron for API Calls

A backend service that lets users schedule HTTP requests to external targets.
"""
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db, close_db
from app.scheduler import scheduler_engine
from app.routers import targets, schedules, runs, metrics

# Static files directory
STATIC_DIR = Path(__file__).parent / "static"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    # Startup
    logger.info("Starting API Scheduler...")
    
    # Initialize database
    await init_db()
    logger.info("Database initialized")
    
    # Start scheduler engine
    await scheduler_engine.start()
    logger.info("Scheduler engine started")
    
    yield
    
    # Shutdown
    logger.info("Shutting down API Scheduler...")
    
    # Stop scheduler
    await scheduler_engine.shutdown()
    
    # Close database
    await close_db()
    
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="API Scheduler",
    description="""
## Cron for API Calls

A backend service that lets users schedule HTTP requests to external targets.

### Features

- **Targets**: Define HTTP endpoints (URL, method, headers, body)
- **Schedules**: Create interval or cron-based schedules
- **Runs**: Track execution history with detailed attempt logs
- **Metrics**: Monitor success rates, latency, and error breakdown

### Scheduling Options

- **Interval**: Run every N seconds
- **Cron**: Use standard cron expressions
- **Window**: Optionally limit duration or max runs

### Error Handling

Automatic retry with exponential backoff. Errors are classified as:
- Timeout
- DNS failure
- Connection error  
- SSL/TLS error
- 4xx Client errors (not retried)
- 5xx Server errors (retried)
    """,
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions."""
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# Include routers
app.include_router(targets.router, prefix="/api/v1")
app.include_router(schedules.router, prefix="/api/v1")
app.include_router(runs.router, prefix="/api/v1")
app.include_router(metrics.router, prefix="/api/v1")


@app.get("/")
async def root():
    """Serve the web UI."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api")
async def api_info():
    """Root API endpoint with information."""
    return {
        "name": "API Scheduler",
        "version": "1.0.0",
        "description": "Cron for API Calls",
        "docs": "/docs",
        "health": "/api/v1/health",
        "ui": "/",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
