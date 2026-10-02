# app/core/database.py
import asyncpg
import logging
import os

log = logging.getLogger(__name__)
_pool = None

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/lacteos_oriente"
)


async def init_db():
    global _pool
    if _pool is None:
        # Parsear la URL para extraer los componentes
        # y agregar SSL requerido por Supabase en producción
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=10,
            command_timeout=60,
            ssl="require"   # ← Requerido por Supabase desde la nube
        )
        async with _pool.acquire() as conn:
            await conn.execute("SELECT 1")
        log.info("✓ PostgreSQL conectado a Supabase")
    return _pool


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("PostgreSQL desconectado")


async def get_db():
    if _pool is None:
        await init_db()
    return _pool


def rec(row):
    """Convierte un asyncpg Record a dict."""
    if row is None:
        return None
    return dict(row)


def recs(rows):
    """Convierte una lista de asyncpg Records a lista de dicts."""
    if not rows:
        return []
    return [dict(r) for r in rows]