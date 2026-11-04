"""
Mustang AI Configuration & Paths
Strict localhost binding, deterministic directories, and security constants.
"""

from pathlib import Path
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MUSTANG_")

    # Base filesystem paths
    APP_NAME: str = "Mustang AI"
    VERSION: str = "0.1.0"
    
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    BACKEND_DIR: Path = BASE_DIR / "backend"
    MODELS_DIR: Path = BASE_DIR / "models"
    DOWNLOADS_DIR: Path = MODELS_DIR / ".downloads"
    RUNTIME_DIR: Path = BACKEND_DIR / "runtime" / "bin"
    RUN_DIR: Path = BACKEND_DIR / "run"

    # Networking (Strict Loopback)
    HOST: str = "127.0.0.1"
    PORT: int = 18000
    
    # Inference Engine (llama-server)
    ENGINE_HOST: str = "127.0.0.1"
    ENGINE_PORT: int = 18080
    ENGINE_STARTUP_TIMEOUT_SECONDS: int = 45
    ENGINE_HEALTH_INTERVAL_SECONDS: float = 2.0
    ENGINE_HUNG_SECONDS: int = 120  # no health answer for this long = hung, restart
    
    # Security
    ALLOWED_ORIGINS: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "tauri://localhost",
        "http://tauri.localhost",  # Tauri 2 WebView2 origin on Windows (measured in M4 Phase B)
    ]
    TOKEN_HEADER_NAME: str = "X-Mustang-Token"
    # 0 = no time limit. Bootstrap is only reachable from an allow-listed Origin and is
    # disabled entirely in desktop mode, where the shell hands the token to the UI over IPC.
    BOOTSTRAP_WINDOW_SECONDS: int = 0

    # Desktop shell integration (M4). Set by the Tauri shell; unset in browser dev mode.
    DESKTOP: bool = False
    SESSION_TOKEN: SecretStr = SecretStr("")
    LOG_DIR: Path | None = None

    @field_validator("HOST", "ENGINE_HOST")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        if value not in ("127.0.0.1", "localhost"):
            raise ValueError(f"Refusing non-loopback bind address {value!r}")
        return value

    # Default Generation Parameters
    DEFAULT_MAX_TOKENS: int = 2048
    DEFAULT_TEMPERATURE: float = 0.7
    DEFAULT_TOP_P: float = 0.9
    DEFAULT_TOP_K: int = 40
    DEFAULT_REPEAT_PENALTY: float = 1.1
    DEFAULT_CONTEXT_SIZE: int = 4096
    DEFAULT_BATCH_SIZE: int = 512


settings = Settings()
settings.MODELS_DIR.mkdir(parents=True, exist_ok=True)
settings.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
settings.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
settings.RUN_DIR.mkdir(parents=True, exist_ok=True)
