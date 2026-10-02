-- ============================================================
--  LÁCTEOS DE ORIENTE — Base de datos PostgreSQL completa
--
--  CÓMO EJECUTARLO:
--  Opción A (Supabase):
--    1. Ve a tu proyecto en supabase.com
--    2. Clic en "SQL Editor" en el menú izquierdo
--    3. Pega TODO este archivo y clic en "Run"
--
--  Opción B (PostgreSQL local):
--    psql -U postgres -d lacteos_oriente -f schema.sql
-- ============================================================

-- Extensiones necesarias
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ============================================================
--  TABLA 1: proveedores
--  Guarda las fincas que entregan leche
-- ============================================================
CREATE TABLE IF NOT EXISTS proveedores (
    id           SERIAL PRIMARY KEY,
    nombre       VARCHAR(120) NOT NULL UNIQUE,
    contacto     VARCHAR(120),
    telefono     VARCHAR(20),
    municipio    VARCHAR(80),
    departamento VARCHAR(80) DEFAULT 'Zacapa',
    activo       BOOLEAN DEFAULT TRUE,
    creado_en    TIMESTAMPTZ DEFAULT NOW()
);

-- Fincas de ejemplo (puedes cambiarlas)
INSERT INTO proveedores (nombre, contacto, municipio) VALUES
    ('Finca El Roble',        'Carlos Pérez',  'Zacapa'),
    ('Hacienda La Esperanza', 'Ana López',      'Chiquimula'),
    ('Granja San Miguel',     'Luis Torres',    'Izabal'),
    ('Finca Las Flores',      'Marta Ruiz',     'Jalapa'),
    ('Estancia El Bosque',    'Jorge Méndez',   'Zacapa')
ON CONFLICT (nombre) DO NOTHING;

-- ============================================================
--  TABLA 2: umbrales_calidad
--  Rangos aceptables de calidad — configurables desde el dashboard
-- ============================================================
CREATE TABLE IF NOT EXISTS umbrales_calidad (
    id             SERIAL PRIMARY KEY,
    nombre         VARCHAR(60) NOT NULL UNIQUE,
    temp_min       NUMERIC(5,2) DEFAULT 2.00,   -- °C mínimo
    temp_max       NUMERIC(5,2) DEFAULT 8.00,   -- °C máximo
    ph_min         NUMERIC(4,2) DEFAULT 6.50,   -- pH mínimo
    ph_max         NUMERIC(4,2) DEFAULT 6.80,   -- pH máximo
    densidad_min   NUMERIC(7,4) DEFAULT 1.0280, -- g/cm³ mínimo
    densidad_max   NUMERIC(7,4) DEFAULT 1.0340, -- g/cm³ máximo
    activo         BOOLEAN DEFAULT TRUE,
    actualizado_en TIMESTAMPTZ DEFAULT NOW(),
    actualizado_por VARCHAR(80)
);

-- Umbral por defecto (el sistema siempre usa el activo)
INSERT INTO umbrales_calidad (nombre) VALUES ('default')
ON CONFLICT (nombre) DO NOTHING;

-- ============================================================
--  TABLA 3: lotes
--  Un lote = una recepción de leche de una finca
--  Combina datos manuales + datos del ESP32
-- ============================================================
CREATE TABLE IF NOT EXISTS lotes (
    id              SERIAL PRIMARY KEY,
    numero_lote     VARCHAR(20) NOT NULL UNIQUE, -- #0001, #0002...
    proveedor_id    INT REFERENCES proveedores(id) ON DELETE SET NULL,
    origen          VARCHAR(120) NOT NULL,        -- Nombre de la finca
    operador        VARCHAR(100) NOT NULL,        -- Quien recibió la leche
    fecha_recepcion DATE    NOT NULL DEFAULT CURRENT_DATE,
    hora_recepcion  TIME    NOT NULL DEFAULT CURRENT_TIME,

    -- Datos que ingresa el operador manualmente
    litros          NUMERIC(8,2)  NOT NULL,
    densidad        NUMERIC(7,4),
    observaciones   TEXT,

    -- Datos automáticos del ESP32
    temperatura     NUMERIC(5,2),
    ph_sensor       NUMERIC(4,2),

    -- pH medido con lactómetro (manual)
    ph_manual       NUMERIC(4,2),

    -- Resultado de clasificación (lo calcula la función clasificar_lote)
    apta            BOOLEAN,
    motivo_rechazo  TEXT,
    umbral_id       INT REFERENCES umbrales_calidad(id) ON DELETE SET NULL,
    creado_en       TIMESTAMPTZ DEFAULT NOW()
);

-- Índices para búsquedas rápidas
CREATE INDEX IF NOT EXISTS idx_lotes_fecha     ON lotes(fecha_recepcion DESC);
CREATE INDEX IF NOT EXISTS idx_lotes_proveedor ON lotes(proveedor_id);
CREATE INDEX IF NOT EXISTS idx_lotes_apta      ON lotes(apta);
CREATE INDEX IF NOT EXISTS idx_lotes_origen    ON lotes USING GIN(origen gin_trgm_ops);

-- ============================================================
--  TABLA 4: mediciones_iot
--  Lecturas continuas del ESP32 (cada 10 segundos)
--  Independiente de los lotes — monitoreo en tiempo real
-- ============================================================
CREATE TABLE IF NOT EXISTS mediciones_iot (
    id          BIGSERIAL PRIMARY KEY,
    temperatura NUMERIC(5,2) NOT NULL,
    ph          NUMERIC(4,2) NOT NULL,
    lote_id     INT REFERENCES lotes(id) ON DELETE SET NULL,
    leida_en    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_med_tiempo ON mediciones_iot(leida_en DESC);
CREATE INDEX IF NOT EXISTS idx_med_lote   ON mediciones_iot(lote_id);

-- ============================================================
--  TABLA 5: alertas
--  Se crea una alerta cuando temperatura o pH salen del rango
-- ============================================================
CREATE TABLE IF NOT EXISTS alertas (
    id          SERIAL PRIMARY KEY,
    tipo        VARCHAR(30) NOT NULL,  -- TEMP_ALTA, PH_BAJO, PH_ALTO, DENSIDAD
    valor       NUMERIC(7,4) NOT NULL, -- El valor que causó la alerta
    limite      NUMERIC(7,4) NOT NULL, -- El límite que se sobrepasó
    lote_id     INT REFERENCES lotes(id)          ON DELETE SET NULL,
    medicion_id BIGINT REFERENCES mediciones_iot(id) ON DELETE SET NULL,
    resuelta    BOOLEAN DEFAULT FALSE,
    creada_en   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alertas_tipo     ON alertas(tipo);
CREATE INDEX IF NOT EXISTS idx_alertas_resuelta ON alertas(resuelta);

-- ============================================================
--  FUNCIÓN: Numeración automática de lotes
--  Al crear un lote sin número, asigna #0001, #0002, etc.
-- ============================================================
CREATE SEQUENCE IF NOT EXISTS seq_numero_lote START 1;

CREATE OR REPLACE FUNCTION generar_numero_lote()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.numero_lote IS NULL OR NEW.numero_lote = '' THEN
        NEW.numero_lote := '#' || LPAD(nextval('seq_numero_lote')::TEXT, 4, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_numero_lote ON lotes;
CREATE TRIGGER trg_numero_lote
    BEFORE INSERT ON lotes
    FOR EACH ROW EXECUTE FUNCTION generar_numero_lote();

-- ============================================================
--  FUNCIÓN: clasificar_lote
--  Esta es la función más importante del sistema.
--  Compara los valores del lote contra los umbrales
--  y decide si la leche es APTA o NO APTA.
--  También genera las alertas correspondientes.
-- ============================================================
CREATE OR REPLACE FUNCTION clasificar_lote(p_lote_id INT)
RETURNS VOID AS $$
DECLARE
    v_lote   lotes%ROWTYPE;
    v_umbral umbrales_calidad%ROWTYPE;
    v_motivo TEXT    := '';
    v_apta   BOOLEAN := TRUE;
    v_ph     NUMERIC(4,2);
BEGIN
    -- Obtener datos del lote y los umbrales activos
    SELECT * INTO v_lote   FROM lotes            WHERE id = p_lote_id;
    SELECT * INTO v_umbral FROM umbrales_calidad WHERE activo = TRUE ORDER BY id LIMIT 1;

    -- Usar pH manual si existe, sino el del sensor
    v_ph := COALESCE(v_lote.ph_manual, v_lote.ph_sensor);

    -- Verificar temperatura
    IF v_lote.temperatura IS NOT NULL THEN
        IF v_lote.temperatura < v_umbral.temp_min OR v_lote.temperatura > v_umbral.temp_max THEN
            v_apta   := FALSE;
            v_motivo := v_motivo || 'Temperatura ' || v_lote.temperatura || '°C fuera de rango ('
                     || v_umbral.temp_min || '–' || v_umbral.temp_max || '°C). ';
            INSERT INTO alertas(tipo,valor,limite,lote_id)
            VALUES('TEMP_ALTA', v_lote.temperatura, v_umbral.temp_max, p_lote_id);
        END IF;
    END IF;

    -- Verificar pH
    IF v_ph IS NOT NULL THEN
        IF v_ph < v_umbral.ph_min THEN
            v_apta   := FALSE;
            v_motivo := v_motivo || 'pH ' || v_ph || ' bajo el mínimo (' || v_umbral.ph_min || '). ';
            INSERT INTO alertas(tipo,valor,limite,lote_id)
            VALUES('PH_BAJO', v_ph, v_umbral.ph_min, p_lote_id);
        ELSIF v_ph > v_umbral.ph_max THEN
            v_apta   := FALSE;
            v_motivo := v_motivo || 'pH ' || v_ph || ' sobre el máximo (' || v_umbral.ph_max || '). ';
            INSERT INTO alertas(tipo,valor,limite,lote_id)
            VALUES('PH_ALTO', v_ph, v_umbral.ph_max, p_lote_id);
        END IF;
    END IF;

    -- Verificar densidad
    IF v_lote.densidad IS NOT NULL THEN
        IF v_lote.densidad < v_umbral.densidad_min OR v_lote.densidad > v_umbral.densidad_max THEN
            v_apta   := FALSE;
            v_motivo := v_motivo || 'Densidad ' || v_lote.densidad || ' g/cm³ fuera de rango. ';
            INSERT INTO alertas(tipo,valor,limite,lote_id)
            VALUES('DENSIDAD', v_lote.densidad, v_umbral.densidad_max, p_lote_id);
        END IF;
    END IF;

    -- Guardar el resultado en el lote
    UPDATE lotes
    SET apta           = v_apta,
        motivo_rechazo = NULLIF(TRIM(v_motivo), ''),
        umbral_id      = v_umbral.id
    WHERE id = p_lote_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
--  VISTAS — Consultas preconstruidas para el frontend
-- ============================================================

-- Vista de trazabilidad completa (une lotes con proveedores)
CREATE OR REPLACE VIEW vista_trazabilidad AS
SELECT
    l.id, l.numero_lote, l.origen, l.operador,
    l.fecha_recepcion, l.hora_recepcion, l.litros,
    l.temperatura,
    COALESCE(l.ph_manual, l.ph_sensor) AS ph,
    l.ph_manual, l.ph_sensor, l.densidad,
    l.apta, l.motivo_rechazo, l.observaciones, l.creado_en,
    p.contacto  AS contacto_proveedor,
    p.municipio AS municipio_proveedor
FROM lotes l
LEFT JOIN proveedores p ON l.proveedor_id = p.id
ORDER BY l.creado_en DESC;

-- Vista de inventario (totales globales)
CREATE OR REPLACE VIEW vista_inventario AS
SELECT
    COUNT(*)                                              AS total_recepciones,
    COALESCE(SUM(litros), 0)                              AS total_litros,
    COALESCE(SUM(litros) FILTER(WHERE apta=TRUE),  0)    AS litros_aptos,
    COALESCE(SUM(litros) FILTER(WHERE apta=FALSE), 0)    AS litros_rechazados,
    ROUND(AVG(litros), 2)                                 AS promedio_litros,
    COUNT(*) FILTER(WHERE fecha_recepcion=CURRENT_DATE)   AS recepciones_hoy,
    COALESCE(SUM(litros) FILTER(WHERE fecha_recepcion=CURRENT_DATE), 0) AS litros_hoy
FROM lotes;
