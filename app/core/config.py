# app/core/config.py
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/lacteos_oriente"
)

PORT        = int(os.getenv("PORT", 8000))
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
IS_PROD     = ENVIRONMENT == "production"

# ── CORS — permite todas las URLs del frontend ────────────────
if IS_PROD:
    CORS_ORIGINS = [
        "https://lacteosdeoriente.online",
        "https://www.lacteosdeoriente.online",
        "https://lacteos-oriente-frontend.vercel.app",
        "http://localhost:3000",
    ]
else:
    CORS_ORIGINS = ["*"]

SECRET_KEY   = os.getenv("SECRET_KEY",           "lacteos_oriente_clave_secreta_2025_jwt")
EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS", 8))

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ACTIVO  = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)