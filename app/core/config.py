import logging
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "Async Site Inspector"
    environment: str = "development"
    log_level: str = "INFO"
    database_path: Path = Path("data/async_site_inspector.db")

    @classmethod
    def from_env(cls) -> "Settings":
        log_level = os.getenv("APP_LOG_LEVEL", "INFO").upper()
        if log_level not in logging.getLevelNamesMapping():
            raise ValueError(f"Unsupported APP_LOG_LEVEL: {log_level}")

        return cls(
            app_name=os.getenv("APP_NAME", "Async Site Inspector"),
            environment=os.getenv("APP_ENVIRONMENT", "development"),
            log_level=log_level,
            database_path=Path(
                os.getenv("APP_DATABASE_PATH", "data/async_site_inspector.db")
            ),
        )
