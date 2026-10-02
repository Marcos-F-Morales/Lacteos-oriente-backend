import asyncpg, logging
from datetime import date, datetime
from app.core.config import DATABASE_URL

logger = logging.getLogger(__name__)
_pool  = None

async def init_db():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=2, max_size=10,
            command_timeout=30, statement_cache_size=0
        )
        async with _pool.acquire() as c:
            await c.execute("SELECT 1")
        logger.info("✓ PostgreSQL conectado")
    return _pool

async def close_db():
    global _pool
    if _pool:
        await _pool.close(); _pool = None

async def get_db():
    global _pool
    if _pool is None: await init_db()
    return _pool

def rec(row):
    if row is None: return None
    d = {}
    for k, v in dict(row).items():
        if isinstance(v, (date, datetime)): d[k] = v.isoformat()
        elif hasattr(v,'__float__') and not isinstance(v,(int,float,bool)): d[k] = float(v)
        else: d[k] = v
    return d

def recs(rows): return [rec(r) for r in rows]
