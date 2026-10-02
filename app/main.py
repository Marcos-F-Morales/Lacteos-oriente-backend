# ============================================================
#  LÁCTEOS DE ORIENTE — Backend FastAPI completo v1.2
#  Incluye: Auth JWT, Roles, IoT, Lotes, Alertas, Telegram Bot
#  CORRER: uvicorn app.main:app --reload --port 8000
#  DOCS:   http://localhost:8000/docs
# ============================================================

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import date, datetime
import asyncio
import logging

from app.core.config         import CORS_ORIGINS, PORT, ENVIRONMENT, TELEGRAM_TOKEN
from app.core.database       import init_db, close_db, get_db, rec, recs
from app.core.auth           import hash_password, verify_password, create_token, decode_token
from app.core.notificaciones import alerta_lote_no_apto, alerta_sensor

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(
    title       = "Lácteos de Oriente — API",
    description = "Sistema IoT de clasificación de calidad de leche. Guatemala.",
    version     = "1.2.0",
    docs_url    = "/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = CORS_ORIGINS,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

@app.on_event("startup")
async def startup():
    await init_db()
    # Inicia el bot de Telegram en segundo plano
    from app.core.bot_telegram import iniciar_bot
    asyncio.create_task(iniciar_bot())

@app.on_event("shutdown")
async def shutdown():
    await close_db()


# ── Seguridad JWT ─────────────────────────────────────────────
security = HTTPBearer(auto_error=False)

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    if not credentials:
        raise HTTPException(401, "Token requerido. Inicia sesión primero.")
    try:
        return decode_token(credentials.credentials)
    except Exception:
        raise HTTPException(401, "Token inválido o expirado. Inicia sesión nuevamente.")

async def require_admin(user=Depends(get_current_user)):
    if user.get("rol") != "administrador":
        raise HTTPException(403, "Acceso denegado. Se requiere rol de administrador.")
    return user


# ============================================================
#  MODELOS
# ============================================================

class DatosIoT(BaseModel):
    temperatura: float         = Field(..., ge=-5,  le=120)
    ph:          float         = Field(..., ge=0,   le=14)
    lote_id:     Optional[int] = None

    @validator('temperatura', 'ph')
    def dos_dec(cls, v): return round(v, 2)


class NuevoLote(BaseModel):
    origen:          str             = Field(..., min_length=2, max_length=120)
    operador:        str             = Field(..., min_length=2, max_length=100)
    litros:          float           = Field(..., gt=0, le=50000)
    densidad:        Optional[float] = Field(None, ge=1.0,  le=1.1)
    ph_manual:       Optional[float] = Field(None, ge=0,    le=14)
    temperatura:     Optional[float] = Field(None, ge=-5,   le=120)
    ph_sensor:       Optional[float] = Field(None, ge=0,    le=14)
    observaciones:   Optional[str]   = Field(None, max_length=1000)
    proveedor_id:    Optional[int]   = None
    fecha_recepcion: Optional[date]  = None
    numero_lote:     Optional[str]   = None

    @validator('origen', 'operador')
    def strip(cls, v): return v.strip()


class Umbrales(BaseModel):
    temp_min:        float         = Field(..., ge=-10, le=50)
    temp_max:        float         = Field(..., ge=-10, le=50)
    ph_min:          float         = Field(..., ge=0,   le=14)
    ph_max:          float         = Field(..., ge=0,   le=14)
    densidad_min:    float         = Field(..., ge=1.0, le=1.1)
    densidad_max:    float         = Field(..., ge=1.0, le=1.1)
    actualizado_por: Optional[str] = None


class LoginData(BaseModel):
    usuario:  str = Field(..., min_length=2)
    password: str = Field(..., min_length=1)


class NuevoUsuario(BaseModel):
    nombre:           str            = Field(..., min_length=2, max_length=100)
    usuario:          str            = Field(..., min_length=3, max_length=60)
    email:            str            = Field(..., min_length=5, max_length=120)
    password:         str            = Field(..., min_length=6)
    rol_id:           int
    activo:           Optional[bool] = True
    telefono:         Optional[str]  = None
    ciudad:           Optional[str]  = None
    telegram_chat_id: Optional[str]  = None


class ActualizarUsuario(BaseModel):
    nombre:           Optional[str]  = None
    email:            Optional[str]  = None
    password:         Optional[str]  = None
    rol_id:           Optional[int]  = None
    activo:           Optional[bool] = None
    telefono:         Optional[str]  = None
    ciudad:           Optional[str]  = None
    telegram_chat_id: Optional[str]  = None


# ============================================================
#  SISTEMA
# ============================================================

@app.get("/", tags=["Sistema"])
async def raiz():
    return {
        "sistema":   "Lácteos de Oriente",
        "version":   "1.2.0",
        "entorno":   ENVIRONMENT,
        "docs":      "/docs",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health", tags=["Sistema"])
async def health():
    try:
        db = await get_db()
        await db.fetchval("SELECT 1")
        return {"estado": "ok", "db": "conectada"}
    except Exception as e:
        raise HTTPException(503, f"DB no disponible: {e}")


# ============================================================
#  AUTENTICACIÓN
# ============================================================

@app.post("/auth/login", tags=["Autenticación"])
async def login(datos: LoginData):
    """Verifica usuario y contraseña. Retorna token JWT + datos completos."""
    db  = await get_db()
    row = await db.fetchrow(
        """SELECT u.id, u.nombre, u.usuario, u.password_hash,
                  u.activo, u.email, u.telefono, u.ciudad,
                  u.telegram_chat_id, r.nombre AS rol
           FROM usuarios u
           JOIN roles r ON u.rol_id = r.id
           WHERE u.usuario = $1""",
        datos.usuario
    )
    if not row:
        raise HTTPException(401, "Usuario no encontrado.")
    if not row['activo']:
        raise HTTPException(403, "Usuario desactivado. Contacta al administrador.")
    if not verify_password(datos.password, row['password_hash']):
        raise HTTPException(401, "Contraseña incorrecta.")

    await db.execute(
        "UPDATE usuarios SET ultimo_login = NOW() WHERE id = $1", row['id']
    )
    token = create_token(row['id'], row['usuario'], row['rol'])
    log.info(f"Login exitoso: {row['usuario']} ({row['rol']})")

    return {
        "token":            token,
        "id":               row['id'],
        "usuario":          row['usuario'],
        "nombre":           row['nombre'],
        "email":            row['email'],
        "rol":              row['rol'],
        "telefono":         row['telefono'],
        "ciudad":           row['ciudad'],
        "telegram_chat_id": row['telegram_chat_id'],
        "initials":         "".join(w[0].upper() for w in row['nombre'].split()[:2])
    }


@app.get("/auth/me", tags=["Autenticación"])
async def me(user=Depends(get_current_user)):
    """Retorna datos completos del usuario actual según el token JWT."""
    db  = await get_db()
    row = await db.fetchrow(
        """SELECT u.id, u.nombre, u.usuario, u.email,
                  u.telefono, u.ciudad, u.telegram_chat_id,
                  u.activo, u.ultimo_login, r.nombre AS rol
           FROM usuarios u
           JOIN roles r ON u.rol_id = r.id
           WHERE u.usuario = $1""",
        user['usuario']
    )
    if not row:
        raise HTTPException(404, "Usuario no encontrado.")
    return rec(row)


# ============================================================
#  ADMINISTRACIÓN — Usuarios
# ============================================================

@app.get("/usuarios", tags=["Administración"])
async def listar_usuarios(admin=Depends(require_admin)):
    db   = await get_db()
    rows = await db.fetch(
        """SELECT u.id, u.nombre, u.usuario, u.email,
                  u.telefono, u.ciudad, u.telegram_chat_id,
                  u.activo, u.creado_en, u.ultimo_login,
                  r.nombre AS rol, r.id AS rol_id
           FROM usuarios u
           JOIN roles r ON u.rol_id = r.id
           ORDER BY u.creado_en DESC"""
    )
    return {"usuarios": recs(rows)}


@app.post("/usuarios", tags=["Administración"], status_code=201)
async def crear_usuario(datos: NuevoUsuario, admin=Depends(require_admin)):
    db = await get_db()
    try:
        uid = await db.fetchval(
            """INSERT INTO usuarios
               (nombre, usuario, email, password_hash, rol_id,
                activo, telefono, ciudad, telegram_chat_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
            datos.nombre,
            datos.usuario.lower().strip(),
            datos.email.lower().strip(),
            hash_password(datos.password),
            datos.rol_id,
            datos.activo,
            datos.telefono,
            datos.ciudad,
            datos.telegram_chat_id
        )
        return {"id": uid, "mensaje": f"Usuario '{datos.usuario}' creado correctamente."}
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(409, "El usuario o email ya existe.")
        raise HTTPException(500, str(e))


@app.put("/usuarios/{uid}", tags=["Administración"])
async def actualizar_usuario(uid: int, datos: ActualizarUsuario, admin=Depends(require_admin)):
    db     = await get_db()
    campos = []
    vals   = []
    i      = 1

    if datos.nombre           is not None: campos.append(f"nombre=${i}");           vals.append(datos.nombre);                   i+=1
    if datos.email            is not None: campos.append(f"email=${i}");            vals.append(datos.email.lower().strip());    i+=1
    if datos.rol_id           is not None: campos.append(f"rol_id=${i}");           vals.append(datos.rol_id);                   i+=1
    if datos.activo           is not None: campos.append(f"activo=${i}");           vals.append(datos.activo);                   i+=1
    if datos.password         is not None: campos.append(f"password_hash=${i}");    vals.append(hash_password(datos.password)); i+=1
    if datos.telefono         is not None: campos.append(f"telefono=${i}");         vals.append(datos.telefono);                 i+=1
    if datos.ciudad           is not None: campos.append(f"ciudad=${i}");           vals.append(datos.ciudad);                   i+=1
    if datos.telegram_chat_id is not None: campos.append(f"telegram_chat_id=${i}"); vals.append(datos.telegram_chat_id);         i+=1

    if not campos:
        raise HTTPException(400, "Sin cambios para aplicar.")

    vals.append(uid)
    await db.execute(
        f"UPDATE usuarios SET {', '.join(campos)} WHERE id=${i}", *vals
    )
    return {"mensaje": "Usuario actualizado correctamente."}


@app.delete("/usuarios/{uid}", tags=["Administración"])
async def eliminar_usuario(uid: int, admin=Depends(require_admin)):
    db = await get_db()
    r  = await db.execute("DELETE FROM usuarios WHERE id=$1", uid)
    if r == "DELETE 0":
        raise HTTPException(404, "Usuario no encontrado.")
    return {"mensaje": "Usuario eliminado."}


# ============================================================
#  ADMINISTRACIÓN — Roles
# ============================================================

@app.get("/roles", tags=["Administración"])
async def listar_roles(admin=Depends(require_admin)):
    db   = await get_db()
    rows = await db.fetch("SELECT * FROM roles ORDER BY id")
    return {"roles": recs(rows)}


@app.post("/roles", tags=["Administración"], status_code=201)
async def crear_rol(
    nombre:      str,
    descripcion: Optional[str] = None,
    admin=Depends(require_admin)
):
    db = await get_db()
    try:
        rid = await db.fetchval(
            "INSERT INTO roles (nombre, descripcion) VALUES ($1, $2) RETURNING id",
            nombre.lower().strip(), descripcion
        )
        return {"id": rid, "mensaje": f"Rol '{nombre}' creado."}
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(409, f"El rol '{nombre}' ya existe.")
        raise HTTPException(500, str(e))


@app.delete("/roles/{rid}", tags=["Administración"])
async def eliminar_rol(rid: int, admin=Depends(require_admin)):
    db    = await get_db()
    count = await db.fetchval(
        "SELECT COUNT(*) FROM usuarios WHERE rol_id=$1", rid
    )
    if count > 0:
        raise HTTPException(409, f"No se puede eliminar: {count} usuario(s) usan este rol.")
    r = await db.execute("DELETE FROM roles WHERE id=$1", rid)
    if r == "DELETE 0":
        raise HTTPException(404, "Rol no encontrado.")
    return {"mensaje": "Rol eliminado."}


# ============================================================
#  IOT
# ============================================================

@app.post("/iot/datos", tags=["IoT"], status_code=201)
async def recibir_iot(d: DatosIoT, bg: BackgroundTasks):
    """ESP32 llama aquí cuando el operador presiona el botón físico."""
    db  = await get_db()
    mid = await db.fetchval(
        "INSERT INTO mediciones_iot(temperatura,ph,lote_id) VALUES($1,$2,$3) RETURNING id",
        d.temperatura, d.ph, d.lote_id
    )
    bg.add_task(_check_alertas, mid, d.temperatura, d.ph, d.lote_id)
    log.info(f"IoT → T:{d.temperatura}°C pH:{d.ph}")
    return {"id": mid, "guardado": True}


async def _check_alertas(mid, temp, ph, lote_id):
    """Revisa umbrales, guarda alertas y notifica por Telegram."""
    try:
        db = await get_db()
        u  = await db.fetchrow(
            "SELECT * FROM umbrales_calidad WHERE activo=TRUE ORDER BY id LIMIT 1"
        )
        if not u: return

        if temp > u['temp_max'] or temp < u['temp_min']:
            await db.execute(
                "INSERT INTO alertas(tipo,valor,limite,medicion_id,lote_id)"
                " VALUES($1,$2,$3,$4,$5)",
                'TEMP_ALTA', temp, u['temp_max'], mid, lote_id
            )
            asyncio.create_task(alerta_sensor('TEMP_ALTA', temp, u['temp_max']))

        if ph < u['ph_min']:
            await db.execute(
                "INSERT INTO alertas(tipo,valor,limite,medicion_id,lote_id)"
                " VALUES($1,$2,$3,$4,$5)",
                'PH_BAJO', ph, u['ph_min'], mid, lote_id
            )
            asyncio.create_task(alerta_sensor('PH_BAJO', ph, u['ph_min']))

        elif ph > u['ph_max']:
            await db.execute(
                "INSERT INTO alertas(tipo,valor,limite,medicion_id,lote_id)"
                " VALUES($1,$2,$3,$4,$5)",
                'PH_ALTO', ph, u['ph_max'], mid, lote_id
            )
            asyncio.create_task(alerta_sensor('PH_ALTO', ph, u['ph_max']))

    except Exception as e:
        log.error(f"Error alertas: {e}")


@app.get("/iot/ultima", tags=["IoT"])
async def ultima():
    """Frontend llama aquí al presionar INICIAR ANÁLISIS."""
    db  = await get_db()
    row = await db.fetchrow(
        "SELECT * FROM mediciones_iot ORDER BY leida_en DESC LIMIT 1"
    )
    if not row:
        return {"datos": None, "mensaje": "Sin mediciones. Enciende el ESP32."}
    return rec(row)


@app.get("/iot/historial", tags=["IoT"])
async def historial(
    horas: int = Query(6,   ge=1, le=720),
    limit: int = Query(200, ge=1, le=2000)
):
    """Datos para las gráficas del Dashboard. Frontend actualiza cada 15s."""
    db   = await get_db()
    rows = await db.fetch(
        "SELECT id,temperatura,ph,leida_en FROM mediciones_iot "
        "WHERE leida_en >= NOW()-($1||' hours')::INTERVAL "
        "ORDER BY leida_en ASC LIMIT $2",
        str(horas), limit
    )
    return {"horas": horas, "total": len(rows), "mediciones": recs(rows)}


@app.get("/iot/resultado", tags=["IoT"])
async def resultado_para_esp32():
    """
    El ESP32 consulta este endpoint cada 5 segundos.
    Le dice si debe encender LED verde (APTA) o rojo (NO APTA).
    """
    db  = await get_db()
    row = await db.fetchrow(
        "SELECT apta, numero_lote, creado_en FROM lotes ORDER BY creado_en DESC LIMIT 1"
    )
    if not row:
        return {"resultado": None, "apta": None}
    return {
        "resultado":   "APTA" if row['apta'] else "NO APTA",
        "apta":        row['apta'],
        "numero_lote": row['numero_lote'],
        "creado_en":   row['creado_en'].isoformat() if row['creado_en'] else None
    }


# ============================================================
#  LOTES
# ============================================================

@app.post("/lotes", tags=["Lotes"], status_code=201)
async def crear_lote(d: NuevoLote):
    """
    Registra una recepción y la clasifica automáticamente.
    Si la finca no existe en proveedores la registra automáticamente.
    Si es NO APTA notifica al administrador por Telegram.
    """
    db = await get_db()

    # ── Auto-registrar la finca si no existe en proveedores ──
    # Así la próxima vez aparece en el dropdown del formulario
    existe = await db.fetchval(
        "SELECT id FROM proveedores WHERE LOWER(nombre) = LOWER($1)",
        d.origen.strip()
    )
    if not existe:
        await db.execute(
            """INSERT INTO proveedores (nombre, activo)
               VALUES ($1, TRUE)
               ON CONFLICT (nombre) DO NOTHING""",
            d.origen.strip().title()
        )
        log.info(f"Finca nueva registrada automáticamente: {d.origen}")

    lid = await db.fetchval(
        """INSERT INTO lotes(origen,operador,litros,densidad,ph_manual,
           temperatura,ph_sensor,observaciones,proveedor_id,
           fecha_recepcion,numero_lote)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,COALESCE($10,CURRENT_DATE),$11)
           RETURNING id""",
        d.origen, d.operador, d.litros, d.densidad, d.ph_manual,
        d.temperatura, d.ph_sensor, d.observaciones, d.proveedor_id,
        d.fecha_recepcion, d.numero_lote
    )
    await db.execute("SELECT clasificar_lote($1)", lid)
    row = await db.fetchrow(
        "SELECT numero_lote, apta, motivo_rechazo FROM lotes WHERE id=$1", lid
    )
    log.info(f"Lote {row['numero_lote']} → {'APTA' if row['apta'] else 'NO APTA'}")

    # Notificar por Telegram si el lote es rechazado
    if not row['apta'] and row['motivo_rechazo']:
        asyncio.create_task(alerta_lote_no_apto(
            row['numero_lote'], d.origen, row['motivo_rechazo'], d.operador
        ))

    return {
        "lote_id":     lid,
        "numero_lote": row['numero_lote'],
        "apta":        row['apta'],
        "motivo":      row['motivo_rechazo'],
        "indicador":   "LED_VERDE" if row['apta'] else "LED_ROJO",
        "mensaje":     "Leche APTA ✓" if row['apta'] else f"NO APTA ✗ — {row['motivo_rechazo']}"
    }


@app.get("/lotes", tags=["Lotes"])
async def listar_lotes(
    limit:  int            = Query(50,  ge=1, le=500),
    apta:   Optional[bool] = Query(None),
    origen: Optional[str]  = Query(None),
    desde:  Optional[date] = Query(None),
    hasta:  Optional[date] = Query(None),
):
    db  = await get_db()
    sql = "SELECT * FROM vista_trazabilidad WHERE 1=1"
    p=[]; i=1
    if apta is not None: sql+=f" AND apta=${i}";             p.append(apta);          i+=1
    if origen:           sql+=f" AND origen ILIKE ${i}";     p.append(f"%{origen}%"); i+=1
    if desde:            sql+=f" AND fecha_recepcion>=${i}"; p.append(desde);         i+=1
    if hasta:            sql+=f" AND fecha_recepcion<=${i}"; p.append(hasta);         i+=1
    sql+=f" ORDER BY creado_en DESC LIMIT ${i}"; p.append(limit)
    rows = await db.fetch(sql, *p)
    return {"total": len(rows), "lotes": recs(rows)}


@app.get("/lotes/{lote_id}", tags=["Lotes"])
async def detalle_lote(lote_id: int):
    db  = await get_db()
    row = await db.fetchrow(
        "SELECT * FROM vista_trazabilidad WHERE id=$1", lote_id
    )
    if not row: raise HTTPException(404, "Lote no encontrado")
    return rec(row)


# ============================================================
#  INVENTARIO
# ============================================================

@app.get("/inventario", tags=["Inventario"])
async def inventario():
    db        = await get_db()
    resumen   = await db.fetchrow("SELECT * FROM vista_inventario")
    semana    = await db.fetch(
        """SELECT fecha_recepcion AS dia, COUNT(*) AS recepciones,
                  COALESCE(SUM(litros),0) AS litros,
                  ROUND(AVG(litros),2) AS promedio,
                  COUNT(*) FILTER(WHERE apta=TRUE)  AS aptas,
                  COUNT(*) FILTER(WHERE apta=FALSE) AS rechazadas
           FROM lotes WHERE fecha_recepcion >= CURRENT_DATE-7
           GROUP BY fecha_recepcion ORDER BY fecha_recepcion DESC"""
    )
    por_finca = await db.fetch(
        """SELECT origen, COUNT(*) AS recepciones,
                  COALESCE(SUM(litros),0) AS total_litros,
                  ROUND(AVG(densidad),4) AS densidad_prom
           FROM lotes WHERE fecha_recepcion >= CURRENT_DATE-30
           GROUP BY origen ORDER BY total_litros DESC LIMIT 10"""
    )
    return {"resumen": rec(resumen), "semana": recs(semana), "por_finca": recs(por_finca)}


# ============================================================
#  TRAZABILIDAD
# ============================================================

@app.get("/trazabilidad", tags=["Trazabilidad"])
async def trazabilidad(
    origen: Optional[str] = Query(None),
    dias:   int           = Query(30, ge=1, le=365)
):
    db  = await get_db()
    sql = """
        SELECT origen, COUNT(*) AS total_recepciones,
               COALESCE(SUM(litros),0) AS total_litros,
               ROUND(AVG(COALESCE(ph_manual,ph_sensor)),2) AS ph_prom,
               ROUND(AVG(temperatura),2) AS temp_prom,
               ROUND(AVG(densidad),4) AS densidad_prom,
               MIN(fecha_recepcion) AS primera_recepcion,
               MAX(fecha_recepcion) AS ultima_recepcion,
               COUNT(*) FILTER(WHERE apta=TRUE)  AS aptas,
               COUNT(*) FILTER(WHERE apta=FALSE) AS rechazadas
        FROM lotes WHERE fecha_recepcion >= CURRENT_DATE-$1
    """
    p=[dias]
    if origen: sql+=" AND origen ILIKE $2"; p.append(f"%{origen}%")
    sql+=" GROUP BY origen ORDER BY total_litros DESC"
    rows = await db.fetch(sql, *p)
    return {"dias": dias, "total_fincas": len(rows), "fincas": recs(rows)}


# ============================================================
#  ALERTAS
# ============================================================

@app.get("/alertas", tags=["Alertas"])
async def listar_alertas(
    horas:    int  = Query(24, ge=1, le=720),
    resuelta: bool = Query(False)
):
    db   = await get_db()
    rows = await db.fetch(
        """SELECT a.id, a.tipo, a.valor, a.limite, a.resuelta, a.creada_en,
                  l.numero_lote, l.origen
           FROM alertas a LEFT JOIN lotes l ON a.lote_id=l.id
           WHERE a.creada_en >= NOW()-($1||' hours')::INTERVAL
             AND a.resuelta=$2
           ORDER BY a.creada_en DESC LIMIT 100""",
        str(horas), resuelta
    )
    return {"total": len(rows), "alertas": recs(rows)}


@app.patch("/alertas/{alerta_id}/resolver", tags=["Alertas"])
async def resolver(alerta_id: int):
    db = await get_db()
    r  = await db.execute(
        "UPDATE alertas SET resuelta=TRUE WHERE id=$1", alerta_id
    )
    if r == "UPDATE 0": raise HTTPException(404, "Alerta no encontrada")
    return {"mensaje": "Alerta resuelta"}


# ============================================================
#  UMBRALES
# ============================================================

@app.get("/umbrales", tags=["Configuración"])
async def get_umbrales():
    """ESP32 descarga esto al encenderse. Frontend lo muestra en Configuración."""
    db  = await get_db()
    row = await db.fetchrow(
        "SELECT * FROM umbrales_calidad WHERE activo=TRUE ORDER BY id LIMIT 1"
    )
    if not row: raise HTTPException(404, "Sin umbrales. Ejecuta el schema.sql.")
    return rec(row)


@app.put("/umbrales", tags=["Configuración"])
async def put_umbrales(d: Umbrales):
    """Actualiza rangos sin reiniciar el sistema ni el ESP32."""
    db = await get_db()
    r  = await db.execute(
        """UPDATE umbrales_calidad
           SET temp_min=$1, temp_max=$2, ph_min=$3, ph_max=$4,
               densidad_min=$5, densidad_max=$6,
               actualizado_en=NOW(), actualizado_por=$7
           WHERE activo=TRUE""",
        d.temp_min, d.temp_max, d.ph_min, d.ph_max,
        d.densidad_min, d.densidad_max, d.actualizado_por
    )
    if r == "UPDATE 0":
        raise HTTPException(500, "No se actualizó. Verifica la tabla umbrales_calidad.")
    return {"mensaje": "Umbrales actualizados correctamente."}


# ============================================================
#  PROVEEDORES
# ============================================================

@app.get("/proveedores", tags=["Proveedores"])
async def get_proveedores():
    """Lista de fincas para el dropdown del formulario de Análisis."""
    db   = await get_db()
    rows = await db.fetch(
        "SELECT id, nombre, contacto, municipio FROM proveedores "
        "WHERE activo=TRUE ORDER BY nombre"
    )
    return {"proveedores": recs(rows)}


@app.post("/proveedores", tags=["Proveedores"], status_code=201)
async def crear_proveedor(
    nombre:       str,
    contacto:     Optional[str] = None,
    telefono:     Optional[str] = None,
    municipio:    Optional[str] = None,
    departamento: Optional[str] = "Zacapa"
):
    db = await get_db()
    try:
        pid = await db.fetchval(
            "INSERT INTO proveedores(nombre,contacto,telefono,municipio,departamento)"
            " VALUES($1,$2,$3,$4,$5) RETURNING id",
            nombre.strip().title(), contacto, telefono, municipio, departamento
        )
        return {"id": pid, "mensaje": f"Proveedor '{nombre}' registrado"}
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(409, f"Ya existe '{nombre}'")
        raise HTTPException(500, str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=True)