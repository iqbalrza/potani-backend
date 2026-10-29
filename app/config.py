"""Pengaturan aplikasi dari environment variable (pydantic-settings)."""
from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CROPS_DIR = Path(__file__).parent / "domain" / "crops"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://potani:potani@localhost:5432/potani"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    log_level: str = "INFO"

    # Auth: "firebase" (produksi) atau "dev" (token "dev:<uid>", hanya untuk pengembangan/test)
    auth_mode: str = "firebase"
    firebase_credentials_file: str | None = None
    firebase_project_id: str | None = None

    # LLM (Groq)
    groq_api_key: str | None = None
    llm_model: str = "openai/gpt-oss-20b"
    llm_temperature: float = 0.2
    llm_timeout_s: float = 20.0
    llm_max_retries: int = 2
    llm_reasoning_effort: str | None = "low"  # hanya untuk model openai/gpt-oss-*
    llm_rate_limit_wait_s: float = 10.0  # total tunggu maksimum saat 429 sebelum fallback deterministik

    # Data domain
    crops_file: str = "crops.json"
    remediation_file: str = "remediation_rules.json"
    suitability_threshold: float = 0.5
    mae_ph: float = 0.5
    mae_moisture: float = 5.0
    mae_temperature: float = 1.0

    # Jeda verifikasi default (hari)
    verify_delay_moisture_days: float = 1.0
    verify_delay_ph_days: float = 56.0
    verify_delay_temperature_days: float = 1.0

    # Sesi & perangkat
    session_window_s: int = 120
    device_offline_minutes: int = 10

    # Closed-loop F6
    missed_after_days: float = 3.0
    max_rereminders: int = 2
    max_replans: int = 3
    f6_max_iterations: int = 6
    f7_max_iterations: int = 5
    plan_max_regenerations: int = 2

    # Demo: 1 = waktu nyata; 1440 = 1 hari disimulasikan dalam 1 menit
    time_acceleration: float = 1.0

    # MQTT
    mqtt_host: str = "localhost"
    mqtt_port: int = 8883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_tls: bool = True
    mqtt_client_id: str = "potani-mqtt-worker"

    # Push
    fcm_enabled: bool = False

    @property
    def demo_mode(self) -> bool:
        return self.time_acceleration != 1.0

    def sim_days(self, days: float) -> timedelta:
        """Ubah jeda hari menjadi timedelta, dipercepat bila TIME_ACCELERATION > 1."""
        return timedelta(days=days) / self.time_acceleration

    def mae(self, param: str) -> float:
        return {"ph": self.mae_ph, "moisture": self.mae_moisture, "temperature": self.mae_temperature}[param]

    def verify_delay_days(self, param: str) -> float:
        return {
            "ph": self.verify_delay_ph_days,
            "moisture": self.verify_delay_moisture_days,
            "temperature": self.verify_delay_temperature_days,
        }[param]

    def crops_path(self) -> Path:
        p = Path(self.crops_file)
        return p if p.is_absolute() else CROPS_DIR / p

    def remediation_path(self) -> Path:
        p = Path(self.remediation_file)
        return p if p.is_absolute() else CROPS_DIR / p


@lru_cache
def get_settings() -> Settings:
    return Settings()
