from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path, *, override: bool = False) -> None:
    """Load a small .env file without adding a runtime dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


ROOT_DIR = Path(__file__).resolve().parents[1]
_load_dotenv(ROOT_DIR / ".env")
RUNTIME_ENV_PATH = Path(os.getenv("RUNTIME_ENV_PATH") or str(ROOT_DIR / ".env"))
if RUNTIME_ENV_PATH.resolve() != (ROOT_DIR / ".env").resolve():
    _load_dotenv(RUNTIME_ENV_PATH, override=True)


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "A股复盘与盘前预案")
    app_env: str = os.getenv("APP_ENV", "development")
    host: str = os.getenv("APP_HOST", "127.0.0.1")
    port: int = int(os.getenv("APP_PORT", "8000"))
    username: str = os.getenv("APP_USERNAME", "")
    password: str = os.getenv("APP_PASSWORD", "")
    data_dir: Path = Path(os.getenv("DATA_DIR", str(ROOT_DIR / "data"))).resolve()

    tushare_token: str = os.getenv("TUSHARE_TOKEN", "")
    tushare_base_url: str = os.getenv("TUSHARE_BASE_URL", "http://api.tushare.pro")
    tushare_points_tier: int = int(os.getenv("TUSHARE_POINTS_TIER", "0"))

    alpha_vantage_api_key: str = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    alpha_vantage_symbols: tuple[str, ...] = tuple(
        value.strip()
        for value in os.getenv("ALPHA_VANTAGE_SYMBOLS", "SPY,QQQ,DIA,SOXX,FXI,KWEB").split(",")
        if value.strip()
    )

    ocr_mode: str = os.getenv("OCR_MODE", "disabled").lower()
    baidu_ocr_api_key: str = os.getenv("BAIDU_OCR_API_KEY", "")
    baidu_ocr_secret_key: str = os.getenv("BAIDU_OCR_SECRET_KEY", "")

    llm_enabled: bool = _bool("LLM_ENABLED")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-chat")

    akshare_enabled: bool = _bool("AKSHARE_ENABLED")
    seesaw_pairs_raw: str = os.getenv("SEESAW_PAIRS", "")

    monitor_enabled: bool = _bool("MONITOR_ENABLED")
    monitor_interval_seconds: int = max(20, int(os.getenv("MONITOR_INTERVAL_SECONDS", "60")))
    monitor_max_symbols: int = max(1, min(20, int(os.getenv("MONITOR_MAX_SYMBOLS", "18"))))

    feishu_enabled: bool = _bool("FEISHU_ENABLED")
    feishu_app_id: str = os.getenv("FEISHU_APP_ID", "")
    feishu_app_secret: str = os.getenv("FEISHU_APP_SECRET", "")
    feishu_allowed_users: str = os.getenv("FEISHU_ALLOWED_USERS", "")
    feishu_notify_chat_id: str = os.getenv("FEISHU_NOTIFY_CHAT_ID", "")

    @property
    def feishu_configured(self) -> bool:
        return bool(self.feishu_enabled and self.feishu_app_id and self.feishu_app_secret)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "market_review.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.username and self.password)

    @property
    def seesaw_pairs(self) -> tuple[tuple[str, str], ...]:
        pairs: list[tuple[str, str]] = []
        for item in self.seesaw_pairs_raw.split(";"):
            left, sep, right = item.partition(":")
            if sep and left.strip() and right.strip():
                pairs.append((left.strip(), right.strip()))
        return tuple(pairs)

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
