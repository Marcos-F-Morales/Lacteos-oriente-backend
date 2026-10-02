# app/core/bot_telegram.py
# Bot de Telegram que responde automáticamente con el Chat ID.
# Corre en segundo plano mientras el backend está activo.
# No necesita URL pública ni webhook.

import logging
import asyncio
import httpx
from app.core.config import TELEGRAM_TOKEN, TELEGRAM_ACTIVO

log = logging.getLogger(__name__)
_ultimo_update_id = 0


async def procesar_mensajes():
    """
    Consulta Telegram cada 3 segundos si hay mensajes nuevos.
    Cuando alguien escribe /start, le responde con su Chat ID.
    """
    global _ultimo_update_id

    if not TELEGRAM_ACTIVO:
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": _ultimo_update_id + 1, "timeout": 2}
            )
            if resp.status_code != 200:
                return

            data    = resp.json()
            updates = data.get("result", [])

            for update in updates:
                _ultimo_update_id = update["update_id"]
                mensaje = update.get("message", {})
                chat_id = mensaje.get("chat", {}).get("id")
                texto   = mensaje.get("text", "")
                nombre  = mensaje.get("chat", {}).get("first_name", "Administrador")

                if chat_id and "/start" in texto:
                    # Responder con el Chat ID automáticamente
                    await client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                        json={
                            "chat_id":    chat_id,
                            "parse_mode": "Markdown",
                            "text": (
                                f"Hola *{nombre}* 👋\n\n"
                                f"Bienvenido al sistema de alertas de\n"
                                f"*Lácteos de Oriente*.\n\n"
                                f"Tu código de notificación es:\n\n"
                                f"`{chat_id}`\n\n"
                                f"Copia ese número y pégalo en tu perfil "
                                f"dentro del sistema, en el campo "
                                f"*'Chat ID de Telegram'*.\n\n"
                                f"A partir de ese momento recibirás "
                                f"las alertas directamente aquí. ✅"
                            )
                        }
                    )
                    log.info(f"Bot respondió a {nombre} con chat_id {chat_id}")

    except Exception as e:
        log.debug(f"Bot polling: {e}")


async def iniciar_bot():
    """Loop principal del bot — corre mientras el servidor está activo."""
    log.info("Bot de Telegram iniciado — escuchando mensajes...")
    while True:
        await procesar_mensajes()
        await asyncio.sleep(3)