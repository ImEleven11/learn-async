import pytest

from app.core.config import Settings


def test_settings_are_loaded_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Configured Inspector")
    monkeypatch.setenv("APP_ENVIRONMENT", "test")
    monkeypatch.setenv("APP_LOG_LEVEL", "debug")

    settings = Settings.from_env()

    assert settings == Settings(
        app_name="Configured Inspector",
        environment="test",
        log_level="DEBUG",
    )


def test_invalid_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_LOG_LEVEL", "verbose")

    with pytest.raises(ValueError, match="Unsupported APP_LOG_LEVEL"):
        Settings.from_env()
