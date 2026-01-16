"""Application configuration."""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Database
    database_url: str = "sqlite+aiosqlite:///./scheduler.db"
    
    # HTTP Client defaults
    default_timeout_seconds: float = 30.0
    max_timeout_seconds: float = 120.0
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    verify_ssl: bool = False  # Set to True in production
    
    # Scheduler
    max_concurrent_jobs: int = 100
    job_misfire_grace_seconds: int = 60
    
    # API
    api_prefix: str = "/api/v1"
    debug: bool = False
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
