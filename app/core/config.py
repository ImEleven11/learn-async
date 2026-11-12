import logging
import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "Async Site Inspector"
    environment: str = "development"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        log_level = os.getenv("APP_LOG_LEVEL", "INFO").upper()
        if log_level not in logging.getLevelNamesMapping():
            raise ValueError(f"Unsupported APP_LOG_LEVEL: {log_level}")

        return cls(
            app_name=os.getenv("APP_NAME", "Async Site Inspector"),
            environment=os.getenv("APP_ENVIRONMENT", "development"),
            log_level=log_level,
        )
