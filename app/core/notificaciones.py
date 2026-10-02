# app/core/notificaciones.py
# Envía alertas de Telegram a TODOS los administradores
# que tengan su Chat ID registrado en su perfil.

import logging
from app.core.config import TELEGRAM_TOKEN, TELEGRAM_ACTIVO, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

DOMINIO = "https://lacteosdeoriente.online"


def enviar_telegram_a(chat_id: str, mensaje: str):
    """Envía un mensaje a un Chat ID específico de Telegram."""
    if not TELEGRAM_ACTIVO or not chat_id:
        log.info(f"[Telegram desactivado] chat_id={chat_id}")
        return
    try:
        import httpx
        resp = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       mensaje,
                "parse_mode": "Markdown"
            },
            timeout=10
        )
        if resp.status_code == 200:
            log.info(f"Telegram enviado a {chat_id}")
        else:
            log.error(f"Error Telegram {chat_id}: {resp.text}")
    except Exception as e:
        log.error(f"Error enviando Telegram: {e}")


async def obtener_chat_ids_admins():
    """
    Obtiene los Chat IDs de Telegram de todos los administradores
    activos que lo tengan registrado en su perfil del sistema.
    """
    from app.core.database import get_db
    db   = await get_db()
    rows = await db.fetch(
        """SELECT u.telegram_chat_id
           FROM usuarios u
           JOIN roles r ON u.rol_id = r.id
           WHERE r.nombre = 'administrador'
             AND u.activo = TRUE
             AND u.telegram_chat_id IS NOT NULL
             AND u.telegram_chat_id != ''"""
    )
    return [row['telegram_chat_id'] for row in rows]


async def notificar_admins(mensaje: str):
    """
    Envía el mensaje a TODOS los administradores con Chat ID registrado.
    Si ninguno tiene, usa el del .env como respaldo.
    """
    import asyncio
    chat_ids = await obtener_chat_ids_admins()

    if not chat_ids and TELEGRAM_CHAT_ID:
        chat_ids = [TELEGRAM_CHAT_ID]

    if not chat_ids:
        log.info("Sin Chat IDs de Telegram configurados.")
        return

    for chat_id in chat_ids:
        await asyncio.to_thread(enviar_telegram_a, chat_id, mensaje)


async def alerta_lote_no_apto(numero_lote: str, origen: str,
                               motivo: str, operador: str = ""):
    """Notifica cuando un lote es clasificado como NO APTA y ya fue guardado."""
    await notificar_admins(
        f"🔴 *ALERTA — Lácteos de Oriente*\n\n"
        f"Lote *{numero_lote}* fue rechazado\n"
        f"📍 Finca: {origen}\n"
        f"👤 Operador: {operador}\n"
        f"⚠️ Motivo: {motivo}\n\n"
        f"🔗 Ver sistema: {DOMINIO}"
    )


async def alerta_analisis_inmediato(finca: str, operador: str,
                                     litros: str, motivo: str):
    """
    Notifica cuando el análisis sale NO APTA EN EL MOMENTO,
    antes de que el operador guarde el registro.
    """
    await notificar_admins(
        f"🔴 *ALERTA — Lácteos de Oriente*\n\n"
        f"Leche NO APTA detectada\n"
        f"📍 Finca: {finca}\n"
        f"👤 Operador: {operador}\n"
        f"⚠️ Motivo: {motivo}\n\n"
        f"🔗 Ver sistema: {DOMINIO}"
    )


async def alerta_sensor(tipo: str, valor: float, limite: float):
    """Notifica cuando un sensor detecta valor fuera de rango."""
    info = {
        "TEMP_ALTA": ("🌡️", "Temperatura Alta",  f"{valor}°C", f"máx {limite}°C"),
        "PH_BAJO":   ("🧪", "pH Bajo",            str(valor),   f"mín {limite}"),
        "PH_ALTO":   ("🧪", "pH Alto",            str(valor),   f"máx {limite}"),
        "DENSIDAD":  ("⚗️", "Densidad Anormal",   str(valor),   f"límite {limite}"),
    }
    icono, nombre, val_txt, lim_txt = info.get(
        tipo, ("⚠️", tipo, str(valor), str(limite))
    )
    await notificar_admins(
        f"{icono} *ALERTA SENSOR — Lácteos de Oriente*\n\n"
        f"*{nombre}*\n"
        f"Valor detectado: `{val_txt}`\n"
        f"Límite configurado: `{lim_txt}`\n\n"
        f"🔗 Ver sistema: {DOMINIO}"
    )