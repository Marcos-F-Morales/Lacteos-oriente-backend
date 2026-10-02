# app/core/auth.py
# Maneja encriptación de contraseñas y tokens JWT

import bcrypt
import jwt
import os
from datetime import datetime, timedelta

SECRET_KEY   = os.getenv("SECRET_KEY", "clave_secreta_cambiar_en_produccion")
EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS", 8))


def hash_password(password: str) -> str:
    """Encripta la contraseña con bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verifica si la contraseña es correcta."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: int, usuario: str, rol: str) -> str:
    """Crea un token JWT con los datos del usuario."""
    payload = {
        "sub":     str(user_id),
        "usuario": usuario,
        "rol":     rol,
        "exp":     datetime.utcnow() + timedelta(hours=EXPIRE_HOURS)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Verifica y decodifica un token JWT. Lanza excepción si es inválido."""
    return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])