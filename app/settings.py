"""Deployment settings. Never hardcode production secrets."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")
    APP_NAME: str = "Цифра"
    ENVIRONMENT: str = "development"
    SECRET_KEY: str = "local-dev-only-change-me-not-for-public-deployment"
    DATABASE_URL: str = f"sqlite:///{ROOT / 'local.sqlite3'}"
    UPLOAD_DIR: str = str(ROOT / "private_uploads")
    MAX_UPLOAD_MB: int = 15
    ALLOWED_HOSTS: str = "localhost,127.0.0.1,testserver"
    S3_BUCKET: str = ""
    S3_ENDPOINT_URL: str = ""
    S3_REGION: str = ""
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    DEMO_MODE: bool = False
    SESSION_HTTPS_ONLY: bool = False
    TRUST_PROXY: bool = False
    ALLOW_EPHEMERAL_MEDIA: bool = False
    PERSISTENT_STORAGE_ROOT: str = ""
    BASE_URL: str = "http://127.0.0.1:8000"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_SSL: bool = False
    UNIVERSITY_WEBHOOK_URL: str = ""
    UNIVERSITY_WEBHOOK_SECRET: str = ""
    UKEP_VERIFY_URL: str = ""
    UKEP_VERIFY_TOKEN: str = ""

    @property
    def production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

settings = Settings()

if settings.production:
    if len(settings.SECRET_KEY) < 32 or settings.SECRET_KEY.startswith("local-dev"):
        raise RuntimeError("Production requires a strong SECRET_KEY (>=32 chars).")
    if not settings.SESSION_HTTPS_ONLY:
        raise RuntimeError("Production requires SESSION_HTTPS_ONLY=true")
    if not settings.S3_BUCKET:
        # File uploads must survive container rebuilds and restarts.
        if not settings.PERSISTENT_STORAGE_ROOT:
            raise RuntimeError("Production requires private S3 or PERSISTENT_STORAGE_ROOT mounted to a real persistent disk.")
        root = Path(settings.PERSISTENT_STORAGE_ROOT).resolve()
        upload_path = Path(settings.UPLOAD_DIR).resolve()
        if upload_path == root or not upload_path.is_relative_to(root):
            raise RuntimeError("UPLOAD_DIR must be a private subdirectory of PERSISTENT_STORAGE_ROOT.")
        if settings.DATABASE_URL.startswith("sqlite:"):
            db_path = Path(settings.DATABASE_URL.replace("sqlite:////", "/", 1)).resolve()
            if not db_path.is_relative_to(root):
                raise RuntimeError("SQLite must be stored on PERSISTENT_STORAGE_ROOT in production.")
    if settings.DEMO_MODE:
        raise RuntimeError("DEMO_MODE cannot be enabled in production.")

UPLOAD_ROOT = Path(settings.UPLOAD_DIR)
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)