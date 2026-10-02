# app/core/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# ── Base de datos ─────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/lacteos_oriente"
)

# ── Servidor ──────────────────────────────────────────────────
PORT        = int(os.getenv("PORT", 8000))
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
IS_PROD     = ENVIRONMENT == "production"

# ── CORS ──────────────────────────────────────────────────────
CORS_ORIGINS = (
    ["*"] if not IS_PROD
    else [
        os.getenv("FRONTEND_URL", "https://lacteos-oriente.vercel.app"),
        "http://localhost:3000"
    ]
)

# ── Autenticación JWT ─────────────────────────────────────────
SECRET_KEY   = os.getenv("SECRET_KEY",           "lacteos_oriente_clave_secreta_2025_jwt")
EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS", 8))

# ── Telegram — Notificaciones al administrador ────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ACTIVO  = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)