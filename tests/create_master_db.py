"""
create_master_db.py — Crea la base de datos maestra de registros depurados.

Esta es la cuarta base de datos del sistema. Almacena registros que
han pasado el proceso de auditoría y fueron verificados como correctos.

Estructura:
  - registros_maestros : Registros ciudadanos verificados y consolidados
  - audit_trail        : Historial de cada acción de auditoría
  - fuentes_verificadas: Qué bases de datos confirmaron cada registro
  - alertas_activas    : Hallazgos pendientes de resolución

Este archivo se corre UNA VEZ para crear el esquema.
Los registros se van poblando desde el dashboard conforme se confirman hallazgos.
"""

import os
import sqlite3
from datetime import datetime

DB_PATH = "tests/data/matriz_certificada.db"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

SCHEMA = """
-- ── Tabla principal: registro ciudadano depurado ──────────────────────────
CREATE TABLE IF NOT EXISTS registros_maestros (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identidad canónica (depurada)
    nombre              TEXT NOT NULL,
    apellido_paterno    TEXT NOT NULL,
    apellido_materno    TEXT,
    nombre_completo     TEXT,
    fecha_nacimiento    TEXT,           -- ISO-8601 YYYY-MM-DD
    edad                INTEGER,
    genero              TEXT,           -- M / F

    -- Contacto
    direccion           TEXT,
    municipio           TEXT,
    telefono            TEXT,
    tipo_telefono       TEXT,

    -- Identificadores (normalizados)
    seguro_social       TEXT,           -- parcial: ***-**-XXXX

    -- Estado y auditoría
    audit_status        TEXT DEFAULT 'verified',
        -- verified | flagged | pending_review | deceased | deleted
    audit_date          TEXT,           -- fecha de última auditoría
    auditado_por        TEXT,           -- usuario del dashboard
    notas_revision      TEXT,

    -- Origen (de cuáles fuentes fue consolidado)
    fuente_cee          TEXT,           -- ID en padrón electoral
    fuente_cesco        TEXT,           -- número de licencia
    fuente_rd           TEXT,           -- certificado RD
    fuente_otro         TEXT,

    -- Metadata
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- ── Historial de auditoría (trail inmutable) ───────────────────────────────
CREATE TABLE IF NOT EXISTS audit_trail (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    registro_id     INTEGER REFERENCES registros_maestros(id),
    accion          TEXT NOT NULL,
        -- confirmed | rejected | edited | flagged | merged | deleted
    usuario         TEXT,
    timestamp       TEXT DEFAULT (datetime('now')),
    notas           TEXT,
    datos_antes     TEXT,               -- JSON: estado antes del cambio
    datos_despues   TEXT                -- JSON: estado después del cambio
);

-- ── Fuentes verificadas por registro ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS fuentes_verificadas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    registro_id     INTEGER REFERENCES registros_maestros(id),
    fuente_nombre   TEXT NOT NULL,      -- CEE | CESCO | RD | MANUAL
    fuente_id       TEXT,               -- ID en la fuente original
    fecha_verificacion TEXT,
    resultado       TEXT,               -- coincide | conflicto | no_encontrado
    notas           TEXT
);

-- ── Alertas activas (hallazgos sin resolver) ───────────────────────────────
CREATE TABLE IF NOT EXISTS alertas_activas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_alerta     TEXT NOT NULL,
        -- CONFIRMED_DECEASED | SSN_CROSS_DB_CONFLICT | NAME_MISMATCH_CROSS_DB
        -- ADDRESS_CLUSTER | NEAR_DUPLICATE | EXACT_DUPLICATE | ANOMALY
    confianza       REAL,               -- 0.0 – 1.0
    registros_ids   TEXT,               -- JSON array de IDs involucrados
    fuentes         TEXT,               -- JSON array de fuentes (CEE, CESCO, RD)
    descripcion     TEXT,
    estado          TEXT DEFAULT 'pendiente',
        -- pendiente | en_revision | resuelto | falso_positivo
    asignado_a      TEXT,
    fecha_creacion  TEXT DEFAULT (datetime('now')),
    fecha_resolucion TEXT,
    notas_resolucion TEXT
);

-- ── Índices para búsquedas rápidas ───────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_maestros_ssn    ON registros_maestros(seguro_social);
CREATE INDEX IF NOT EXISTS idx_maestros_nombre ON registros_maestros(nombre, apellido_paterno);
CREATE INDEX IF NOT EXISTS idx_maestros_dob    ON registros_maestros(fecha_nacimiento);
CREATE INDEX IF NOT EXISTS idx_maestros_mun    ON registros_maestros(municipio);
CREATE INDEX IF NOT EXISTS idx_alertas_tipo    ON alertas_activas(tipo_alerta, estado);
CREATE INDEX IF NOT EXISTS idx_audit_registro  ON audit_trail(registro_id, timestamp);
"""

conn = sqlite3.connect(DB_PATH)
conn.executescript(SCHEMA)

# Insertar metadata de la base
conn.execute("""
    CREATE TABLE IF NOT EXISTS db_meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
""")
meta = [
    ("version",         "0.7.0"),
    ("created_at",      datetime.now().isoformat()),
    ("description",     "Matriz certificada de registros depurados — scanner_agent"),
    ("fuentes_activas", '["CEE", "CESCO", "RD"]'),
    ("owner",           "Sabias que PR — audit intelligence project"),
]
conn.executemany("INSERT OR REPLACE INTO db_meta (key,value) VALUES (?,?)", meta)
conn.commit()

tables = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()
conn.close()

print(f"matriz_certificada.db creada: {DB_PATH}")
print(f"  Tablas creadas:")
for (t,) in tables:
    print(f"    - {t}")
print()
print("  Este archivo es el destino final de registros verificados.")
print("  Se puebla desde el dashboard conforme se resuelven hallazgos.")
