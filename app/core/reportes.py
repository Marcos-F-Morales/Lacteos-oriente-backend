# app/core/reportes.py
from fastapi import APIRouter, Query
from typing import Optional
from datetime import datetime, timedelta
from app.core.database import get_db, recs, rec

router = APIRouter(prefix="/reportes", tags=["Reportes"])


# ── Helper: rango de fechas ────────────────────────────────────────────
def _rango(periodo: str):
    hoy = datetime.utcnow()
    if periodo == "dia":
        inicio = hoy.replace(hour=0, minute=0, second=0, microsecond=0)
    elif periodo == "semana":
        inicio = (hoy - timedelta(days=hoy.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif periodo == "mes":
        inicio = hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:  # "todo"
        inicio = datetime(2000, 1, 1)
    return inicio, hoy


# ── GET /reportes/lotes ────────────────────────────────────────────────
@router.get("/lotes")
async def reporte_lotes(
    periodo: str = Query("mes", enum=["dia", "semana", "mes", "todo"]),
    finca: Optional[str] = Query(None),
):
    inicio, fin = _rango(periodo)
    pool = await get_db()

    async with pool.acquire() as conn:
        params = [inicio, fin]
        filtro_finca = ""
        if finca:
            params.append(f"%{finca}%")
            filtro_finca = f"AND (l.origen ILIKE ${len(params)} OR p.nombre ILIKE ${len(params)})"

        rows = await conn.fetch(f"""
            SELECT
                l.id,
                l.numero_lote,
                l.origen,
                COALESCE(p.nombre, l.origen) AS finca,
                l.litros,
                l.ph_manual,
                l.ph_sensor,
                l.temperatura_sensor,
                l.apta,
                l.motivo_rechazo,
                l.creado_en
            FROM lotes l
            LEFT JOIN proveedores p ON p.id = l.proveedor_id
            WHERE l.creado_en BETWEEN $1 AND $2
            {filtro_finca}
            ORDER BY l.creado_en DESC
        """, *params)

    lotes = recs(rows)
    total_litros  = sum(float(r["litros"] or 0) for r in lotes)
    aptas         = sum(1 for r in lotes if r["apta"])
    no_aptas      = len(lotes) - aptas

    return {
        "periodo":      periodo,
        "finca_filtro": finca,
        "total_lotes":  len(lotes),
        "total_litros": total_litros,
        "aptas":        aptas,
        "no_aptas":     no_aptas,
        "lotes":        lotes,
    }


# ── GET /reportes/inventario ───────────────────────────────────────────
@router.get("/inventario")
async def reporte_inventario(
    periodo: str = Query("mes", enum=["dia", "semana", "mes", "todo"]),
):
    inicio, fin = _rango(periodo)
    pool = await get_db()

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                COALESCE(p.nombre, l.origen, 'Sin finca')          AS finca,
                COUNT(*)                                            AS total_lotes,
                SUM(l.litros)                                       AS total_litros,
                SUM(CASE WHEN l.apta     THEN l.litros ELSE 0 END) AS litros_aptos,
                SUM(CASE WHEN NOT l.apta THEN l.litros ELSE 0 END) AS litros_rechazados,
                AVG(COALESCE(l.ph_manual, l.ph_sensor))            AS ph_promedio,
                AVG(l.temperatura_sensor)                           AS temp_promedio
            FROM lotes l
            LEFT JOIN proveedores p ON p.id = l.proveedor_id
            WHERE l.creado_en BETWEEN $1 AND $2
            GROUP BY COALESCE(p.nombre, l.origen, 'Sin finca')
            ORDER BY total_litros DESC
        """, inicio, fin)

        global_row = await conn.fetchrow("""
            SELECT
                COUNT(*)                                            AS total_lotes,
                SUM(l.litros)                                       AS total_litros,
                SUM(CASE WHEN l.apta     THEN l.litros ELSE 0 END) AS litros_aptos,
                SUM(CASE WHEN NOT l.apta THEN l.litros ELSE 0 END) AS litros_rechazados
            FROM lotes l
            WHERE l.creado_en BETWEEN $1 AND $2
        """, inicio, fin)

    return {
        "periodo":         periodo,
        "resumen_global":  rec(global_row),
        "por_finca":       recs(rows),
    }


# ── GET /reportes/alertas ──────────────────────────────────────────────
@router.get("/alertas")
async def reporte_alertas(
    periodo: str = Query("mes", enum=["dia", "semana", "mes", "todo"]),
):
    inicio, fin = _rango(periodo)
    pool = await get_db()

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                a.id,
                a.tipo,
                a.mensaje,
                a.resuelta,
                a.creado_en,
                l.numero_lote
            FROM alertas a
            LEFT JOIN lotes l ON l.id = a.lote_id
            WHERE a.creado_en BETWEEN $1 AND $2
            ORDER BY a.creado_en DESC
        """, inicio, fin)

    alertas = recs(rows)
    return {
        "periodo":    periodo,
        "total":      len(alertas),
        "resueltas":  sum(1 for a in alertas if a["resuelta"]),
        "pendientes": sum(1 for a in alertas if not a["resuelta"]),
        "alertas":    alertas,
    }