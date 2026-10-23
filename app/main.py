# app/main.py
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from contextlib import asynccontextmanager
from typing import Optional
from datetime import datetime
import asyncpg

from app.core.config import CORS_ORIGINS
from app.core.database import get_db, recs, rec
from app.core.auth import crear_token, verificar_token, hashear_password, verificar_password
from app.core.reportes import router as reportes_router

# ── Modelos Pydantic ───────────────────────────────────────────
from pydantic import BaseModel

class LoginData(BaseModel):
    username: str
    password: str

class LoteData(BaseModel):
    numero_lote:    str
    origen:         str
    litros:         float
    ph_manual:      Optional[float] = None
    temperatura:    Optional[float] = None
    observaciones:  Optional[str]   = None
    proveedor_id:   Optional[int]   = None

class UmbralData(BaseModel):
    ph_min:        float
    ph_max:        float
    temp_min:      float
    temp_max:      float
    acidez_max:    Optional[float] = None
    densidad_min:  Optional[float] = None
    densidad_max:  Optional[float] = None

class UsuarioData(BaseModel):
    nombre:   str
    username: str
    password: str
    rol_id:   int
    activo:   Optional[bool] = True

class UsuarioUpdate(BaseModel):
    nombre:   Optional[str]  = None
    username: Optional[str]  = None
    password: Optional[str]  = None
    rol_id:   Optional[int]  = None
    activo:   Optional[bool] = None

class AlertaTelegramData(BaseModel):
    mensaje: str

# ── App ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_db()   # inicializa pool
    yield

app = FastAPI(title="Lácteos de Oriente API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(reportes_router)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# ── Helpers auth ───────────────────────────────────────────────
async def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = verificar_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    pool = await get_db()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT u.*, r.nombre AS rol FROM usuarios u JOIN roles r ON r.id = u.rol_id WHERE u.id = $1",
            payload["sub"]
        )
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return dict(user)

# ── Auth ───────────────────────────────────────────────────────
@app.post("/auth/login")
async def login(form: OAuth2PasswordRequestForm = Depends()):
    pool = await get_db()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT u.*, r.nombre AS rol FROM usuarios u JOIN roles r ON r.id = u.rol_id WHERE u.username = $1 AND u.activo = true",
            form.username
        )
    if not user or not verificar_password(form.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    token = crear_token({"sub": user["id"], "rol": user["rol"]})
    return {"access_token": token, "token_type": "bearer", "rol": user["rol"], "nombre": user["nombre"]}

@app.get("/auth/me")
async def me(current_user=Depends(get_current_user)):
    return {k: v for k, v in current_user.items() if k != "password_hash"}

# ── IoT ────────────────────────────────────────────────────────
@app.get("/iot/ultima")
async def ultima_medicion():
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM mediciones_iot ORDER BY creado_en DESC LIMIT 1"
        )
    if not row:
        raise HTTPException(status_code=404, detail="Sin mediciones")
    return dict(row)

@app.get("/iot/historial")
async def historial_iot(horas: int = 6, limit: int = 200):
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM mediciones_iot WHERE creado_en >= NOW() - ($1 || ' hours')::interval ORDER BY creado_en DESC LIMIT $2",
            str(horas), limit
        )
    return [dict(r) for r in rows]

@app.post("/iot/medicion")
async def recibir_medicion(datos: dict):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO mediciones_iot (ph_sensor, temperatura, humedad, creado_en) VALUES ($1, $2, $3, NOW())",
            datos.get("ph"), datos.get("temperatura"), datos.get("humedad")
        )
    return {"ok": True}

# ── Lotes ──────────────────────────────────────────────────────
@app.post("/lotes")
async def crear_lote(d: LoteData, current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:

        # 1. Auto-registrar finca en proveedores si no existe
        await conn.execute(
            """
            INSERT INTO proveedores (nombre, activo)
            VALUES ($1, true)
            ON CONFLICT (nombre) DO NOTHING
            """,
            d.origen.strip()
        )

        # 2. Obtener proveedor_id por nombre (fix: ya no queda NULL)
        proveedor_id = d.proveedor_id or await conn.fetchval(
            "SELECT id FROM proveedores WHERE LOWER(nombre) = LOWER($1)",
            d.origen.strip()
        )

        # 3. Obtener umbrales de calidad
        umbrales = await conn.fetchrow("SELECT * FROM umbrales_calidad ORDER BY id DESC LIMIT 1")

        # 4. Evaluar aptitud
        apta = True
        motivo_rechazo = None
        if umbrales and d.ph_manual is not None:
            if not (umbrales["ph_min"] <= d.ph_manual <= umbrales["ph_max"]):
                apta = False
                motivo_rechazo = f"pH fuera de rango ({d.ph_manual})"
        if umbrales and d.temperatura is not None:
            if not (umbrales["temp_min"] <= d.temperatura <= umbrales["temp_max"]):
                apta = False
                motivo = f"Temperatura fuera de rango ({d.temperatura}°C)"
                motivo_rechazo = f"{motivo_rechazo}, {motivo}" if motivo_rechazo else motivo

        # 5. Insertar lote
        lote_id = await conn.fetchval(
            """
            INSERT INTO lotes
              (numero_lote, origen, litros, ph_manual, temperatura,
               observaciones, proveedor_id, apta, motivo_rechazo,
               fecha_recepcion, creado_en)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW())
            RETURNING id
            """,
            d.numero_lote, d.origen.strip(), d.litros,
            d.ph_manual, d.temperatura,
            d.observaciones, proveedor_id, apta, motivo_rechazo
        )

        # 6. Registrar alerta si no apta
        if not apta:
            await conn.execute(
                """
                INSERT INTO alertas (tipo, valor, limite, lote_id, resuelta, creada_en)
                VALUES ($1, $2, $3, $4, false, NOW())
                """,
                "CALIDAD",
                d.ph_manual or d.temperatura,
                umbrales["ph_max"] if d.ph_manual else umbrales["temp_max"],
                lote_id
            )

    return {"id": lote_id, "apta": apta, "motivo_rechazo": motivo_rechazo, "proveedor_id": proveedor_id}

@app.get("/lotes")
async def listar_lotes(
    limit:  int           = 50,
    offset: int           = 0,
    apta:   Optional[bool]= None,
    current_user=Depends(get_current_user)
):
    pool = await get_db()
    async with pool.acquire() as conn:
        where = "WHERE 1=1"
        params = []
        if apta is not None:
            params.append(apta)
            where += f" AND l.apta = ${len(params)}"
        params += [limit, offset]
        rows = await conn.fetch(
            f"""
            SELECT l.*, p.nombre AS finca
            FROM lotes l
            LEFT JOIN proveedores p ON p.id = l.proveedor_id
            {where}
            ORDER BY l.creado_en DESC
            LIMIT ${len(params)-1} OFFSET ${len(params)}
            """,
            *params
        )
    return [dict(r) for r in rows]

@app.get("/lotes/{lote_id}")
async def obtener_lote(lote_id: int, current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT l.*, p.nombre AS finca
            FROM lotes l LEFT JOIN proveedores p ON p.id = l.proveedor_id
            WHERE l.id = $1
            """,
            lote_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Lote no encontrado")
    return dict(row)

# ── Inventario ─────────────────────────────────────────────────
@app.get("/inventario")
async def inventario(current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                COALESCE(p.nombre, l.origen) AS finca,
                COUNT(*)                      AS total_lotes,
                SUM(l.litros)                 AS total_litros,
                SUM(CASE WHEN l.apta THEN l.litros ELSE 0 END)     AS litros_aptos,
                SUM(CASE WHEN NOT l.apta THEN l.litros ELSE 0 END)  AS litros_rechazados
            FROM lotes l
            LEFT JOIN proveedores p ON p.id = l.proveedor_id
            GROUP BY COALESCE(p.nombre, l.origen)
            ORDER BY total_litros DESC
            """
        )
    return [dict(r) for r in rows]

# ── Trazabilidad ───────────────────────────────────────────────
@app.get("/trazabilidad")
async def trazabilidad(
    finca:  Optional[str]  = None,
    estado: Optional[str]  = None,
    limit:  int            = 100,
    offset: int            = 0,
    current_user=Depends(get_current_user)
):
    pool = await get_db()
    async with pool.acquire() as conn:
        filtros  = []
        params   = []

        if finca:
            params.append(f"%{finca}%")
            filtros.append(f"(LOWER(p.nombre) LIKE LOWER(${len(params)}) OR LOWER(l.origen) LIKE LOWER(${len(params)}))")
        if estado == "apta":
            filtros.append("l.apta = true")
        elif estado == "rechazada":
            filtros.append("l.apta = false")

        where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
        params += [limit, offset]

        rows = await conn.fetch(
            f"""
            SELECT l.*, COALESCE(p.nombre, l.origen) AS finca
            FROM lotes l
            LEFT JOIN proveedores p ON p.id = l.proveedor_id
            {where}
            ORDER BY l.creado_en DESC
            LIMIT ${len(params)-1} OFFSET ${len(params)}
            """,
            *params
        )
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM lotes l LEFT JOIN proveedores p ON p.id = l.proveedor_id {where}",
            *params[:-2]
        )
    return {"total": total, "lotes": [dict(r) for r in rows]}

# ── Alertas ────────────────────────────────────────────────────
@app.get("/alertas")
async def listar_alertas(
    horas:    int            = 24,
    resuelta: Optional[bool] = None,
    current_user=Depends(get_current_user)
):
    pool = await get_db()
    async with pool.acquire() as conn:
        where  = "WHERE a.creada_en >= NOW() - ($1 || ' hours')::interval"
        params = [str(horas)]
        if resuelta is not None:
            params.append(resuelta)
            where += f" AND a.resuelta = ${len(params)}"
        rows = await conn.fetch(
            f"""
            SELECT a.*, l.numero_lote
            FROM alertas a
            LEFT JOIN lotes l ON l.id = a.lote_id
            {where}
            ORDER BY a.creada_en DESC
            """,
            *params
        )
    return [dict(r) for r in rows]

@app.patch("/alertas/{alerta_id}/resolver")
async def resolver_alerta(alerta_id: int, current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE alertas SET resuelta = true WHERE id = $1",
            alerta_id
        )
    return {"ok": True}

@app.post("/alertas/telegram")
async def enviar_telegram(datos: AlertaTelegramData, current_user=Depends(get_current_user)):
    # Implementar con bot de Telegram si se requiere
    return {"ok": True, "mensaje": datos.mensaje}

# ── Umbrales ───────────────────────────────────────────────────
@app.get("/umbrales")
async def obtener_umbrales(current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM umbrales_calidad ORDER BY id DESC LIMIT 1")
    if not row:
        raise HTTPException(status_code=404, detail="Sin umbrales configurados")
    return dict(row)

@app.put("/umbrales")
async def actualizar_umbrales(datos: UmbralData, current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM umbrales_calidad ORDER BY id DESC LIMIT 1")
        if existing:
            await conn.execute(
                """
                UPDATE umbrales_calidad
                SET ph_min=$1, ph_max=$2, temp_min=$3, temp_max=$4,
                    acidez_max=$5, densidad_min=$6, densidad_max=$7
                WHERE id=$8
                """,
                datos.ph_min, datos.ph_max, datos.temp_min, datos.temp_max,
                datos.acidez_max, datos.densidad_min, datos.densidad_max,
                existing["id"]
            )
        else:
            await conn.execute(
                """
                INSERT INTO umbrales_calidad
                  (ph_min, ph_max, temp_min, temp_max, acidez_max, densidad_min, densidad_max)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                """,
                datos.ph_min, datos.ph_max, datos.temp_min, datos.temp_max,
                datos.acidez_max, datos.densidad_min, datos.densidad_max
            )
    return {"ok": True}

# ── Proveedores ────────────────────────────────────────────────
@app.get("/proveedores")
async def listar_proveedores(current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM proveedores WHERE activo = true ORDER BY nombre")
    return [dict(r) for r in rows]

# ── Usuarios ───────────────────────────────────────────────────
@app.get("/usuarios")
async def listar_usuarios(current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT u.id, u.nombre, u.username, u.activo, u.rol_id, r.nombre AS rol FROM usuarios u JOIN roles r ON r.id = u.rol_id ORDER BY u.nombre"
        )
    return [dict(r) for r in rows]

@app.post("/usuarios")
async def crear_usuario(datos: UsuarioData, current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        existe = await conn.fetchval("SELECT id FROM usuarios WHERE username = $1", datos.username)
        if existe:
            raise HTTPException(status_code=400, detail="Username ya existe")
        uid = await conn.fetchval(
            "INSERT INTO usuarios (nombre, username, password_hash, rol_id, activo) VALUES ($1,$2,$3,$4,$5) RETURNING id",
            datos.nombre, datos.username, hashear_password(datos.password), datos.rol_id, datos.activo
        )
    return {"id": uid}

@app.put("/usuarios/{uid}")
async def actualizar_usuario(uid: int, datos: UsuarioUpdate, current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        if datos.nombre is not None:
            await conn.execute("UPDATE usuarios SET nombre=$1 WHERE id=$2", datos.nombre, uid)
        if datos.username is not None:
            await conn.execute("UPDATE usuarios SET username=$1 WHERE id=$2", datos.username, uid)
        if datos.password is not None:
            await conn.execute("UPDATE usuarios SET password_hash=$1 WHERE id=$2", hashear_password(datos.password), uid)
        if datos.rol_id is not None:
            await conn.execute("UPDATE usuarios SET rol_id=$1 WHERE id=$2", datos.rol_id, uid)
        if datos.activo is not None:
            await conn.execute("UPDATE usuarios SET activo=$1 WHERE id=$2", datos.activo, uid)
    return {"ok": True}

@app.delete("/usuarios/{uid}")
async def eliminar_usuario(uid: int, current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE usuarios SET activo=false WHERE id=$1", uid)
    return {"ok": True}

# ── Roles ──────────────────────────────────────────────────────
@app.get("/roles")
async def listar_roles(current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM roles ORDER BY nombre")
    return [dict(r) for r in rows]

@app.post("/roles")
async def crear_rol(nombre: str = Query(...), descripcion: str = Query(""), current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        rid = await conn.fetchval(
            "INSERT INTO roles (nombre, descripcion) VALUES ($1,$2) RETURNING id",
            nombre, descripcion
        )
    return {"id": rid}

@app.delete("/roles/{rol_id}")
async def eliminar_rol(rol_id: int, current_user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM roles WHERE id=$1", rol_id)
    return {"ok": True}