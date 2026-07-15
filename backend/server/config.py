import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_ROOT.parent
PROJECT_ROOT = BACKEND_ROOT.parent
APP_ROOT = Path(os.getenv("APP_ROOT", BACKEND_ROOT))

RUNTIME_DIR = Path(os.getenv("RUNTIME_DIR", APP_ROOT / ".runtime"))
GENERATED_REPORT_DIR = Path(
    os.getenv("GENERATED_REPORT_DIR", APP_ROOT / "generated_report")
)
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", APP_ROOT / "users.db"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATABASE_PATH.as_posix()}")

FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173",
    ).split(",")
    if origin.strip()
]

SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "session_id")
SESSION_COOKIE_MAX_AGE = int(os.getenv("SESSION_COOKIE_MAX_AGE", str(60 * 60 * 24 * 7)))
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax")
