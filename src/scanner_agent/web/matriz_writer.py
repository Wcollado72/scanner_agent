"""
scanner_agent.web.matriz_writer  v0.7.0

Escribe hallazgos confirmados desde el dashboard a matriz_certificada.db.
Se invoca UNO POR UNO cuando el auditor confirma un hallazgo individual.

Flujo:
  1. El auditor revisa un finding en el dashboard
  2. Selecciona la accion_tomada (ej. BAJA_DEFUNCION, CORRECCION_NOMBRE)
  3. Presiona "Confirmar y Certificar"
  4. Este módulo escribe a matriz_certificada.db:
       - registros_maestros  : el/los registro(s) canónicos depurados
       - alertas_activas     : el hallazgo marcado como resuelto
       - audit_trail         : registro inmutable de la accion
       - fuentes_verificadas : qué bases confirmaron cada registro
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Acciones válidas por tipo de flag ─────────────────────────────────────
ACCIONES_POR_FLAG: dict[str, list[tuple[str, str]]] = {
    "CONFIRMED_DECEASED": [
        ("BAJA_DEFUNCION",        "Baja por defunción confirmada"),
        ("PENDIENTE_VERIFICACION","Pendiente — requiere certificado físico"),
    ],
    "NAME_MISMATCH_CROSS_DB": [
        ("CORRECCION_NOMBRE",     "Corregir nombre en padrón CEE"),
        ("NOTIFICAR_ELECTOR",     "Notificar elector para corrección voluntaria"),
        ("INVESTIGACION_ADICIONAL","Investigación adicional requerida"),
    ],
    "SSN_CROSS_DB_CONFLICT": [
        ("INVESTIGACION_SSN",     "Referir a investigación de SSN"),
        ("CORRECCION_SSN",        "Corregir SSN erróneo identificado"),
        ("ALERTA_FRAUDE",         "Alerta de posible fraude de identidad"),
    ],
    "EXACT_DUPLICATE": [
        ("BAJA_DUPLICADO",        "Baja del registro duplicado"),
        ("FUSION_REGISTROS",      "Fusionar registros en uno canónico"),
    ],
    "NEAR_DUPLICATE": [
        ("BAJA_DUPLICADO",        "Baja del registro duplicado"),
        ("FUSION_REGISTROS",      "Fusionar — misma persona, datos distintos"),
        ("NOTIFICAR_ELECTOR",     "Notificar elector para verificación"),
    ],
    "ADDRESS_CLUSTER": [
        ("VERIFICACION_DOMICILIO","Verificación física de domicilio"),
        ("BAJA_DIRECCION_INVALIDA","Baja por dirección no residencial"),
        ("INVESTIGACION_ADICIONAL","Investigación adicional requerida"),
    ],
    "POSSIBLE_DECEASED": [
        ("VERIFICACION_DEFUNCION", "Verificar con Registro Demográfico"),
        ("BAJA_DEFUNCION",         "Baja confirmada por defunción"),
        ("REGISTRO_VIGENTE",       "Registro vigente — persona viva"),
    ],
    "ANOMALY": [
        ("CORRECCION_DATOS",      "Corregir datos erróneos"),
        ("BAJA_REGISTRO_INVALIDO","Baja por registro inválido"),
        ("INVESTIGACION_ADICIONAL","Investigación adicional requerida"),
    ],
}

# Estado audit que se graba en registros_maestros según accion_tomada
_STATUS_MAP: dict[str, str] = {
    "BAJA_DEFUNCION":         "deceased",
    "BAJA_DUPLICADO":         "deleted",
    "BAJA_DIRECCION_INVALIDA":"deleted",
    "BAJA_REGISTRO_INVALIDO": "deleted",
    "CORRECCION_NOMBRE":      "verified",
    "CORRECCION_SSN":         "verified",
    "CORRECCION_DATOS":       "verified",
    "FUSION_REGISTROS":       "verified",
    "NOTIFICAR_ELECTOR":      "pending_review",
    "INVESTIGACION_SSN":      "flagged",
    "ALERTA_FRAUDE":          "flagged",
    "INVESTIGACION_ADICIONAL":"flagged",
    "VERIFICACION_DOMICILIO": "pending_review",
    "VERIFICACION_DEFUNCION": "pending_review",
    "PENDIENTE_VERIFICACION": "pending_review",
    "REGISTRO_VIGENTE":       "verified",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Crea las tablas si no existen (idempotente)."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS registros_maestros (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre              TEXT NOT NULL,
        apellido_paterno    TEXT NOT NULL,
        apellido_materno    TEXT,
        nombre_completo     TEXT,
        fecha_nacimiento    TEXT,
        edad                INTEGER,
        genero              TEXT,
        direccion           TEXT,
        municipio           TEXT,
        telefono            TEXT,
        tipo_telefono       TEXT,
        seguro_social       TEXT,
        audit_status        TEXT DEFAULT 'verified',
        audit_date          TEXT,
        auditado_por        TEXT,
        notas_revision      TEXT,
        accion_tomada       TEXT,
        flag_origen         TEXT,
        fuente_cee          TEXT,
        fuente_cesco        TEXT,
        fuente_rd           TEXT,
        fuente_otro         TEXT,
        created_at          TEXT DEFAULT (datetime('now')),
        updated_at          TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS audit_trail (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        registro_id     INTEGER REFERENCES registros_maestros(id),
        accion          TEXT NOT NULL,
        flag_origen     TEXT,
        accion_tomada   TEXT,
        usuario         TEXT,
        timestamp       TEXT DEFAULT (datetime('now')),
        notas           TEXT,
        datos_antes     TEXT,
        datos_despues   TEXT
    );

    CREATE TABLE IF NOT EXISTS fuentes_verificadas (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        registro_id         INTEGER REFERENCES registros_maestros(id),
        fuente_nombre       TEXT NOT NULL,
        fuente_id           TEXT,
        fecha_verificacion  TEXT,
        resultado           TEXT,
        notas               TEXT
    );

    CREATE TABLE IF NOT EXISTS alertas_activas (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo_alerta         TEXT NOT NULL,
        confianza           REAL,
        registros_ids       TEXT,
        fuentes             TEXT,
        descripcion         TEXT,
        estado              TEXT DEFAULT 'pendiente',
        asignado_a          TEXT,
        fecha_creacion      TEXT DEFAULT (datetime('now')),
        fecha_resolucion    TEXT,
        notas_resolucion    TEXT
    );

    CREATE TABLE IF NOT EXISTS db_meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_maestros_ssn    ON registros_maestros(seguro_social);
    CREATE INDEX IF NOT EXISTS idx_maestros_nombre ON registros_maestros(nombre, apellido_paterno);
    CREATE INDEX IF NOT EXISTS idx_maestros_dob    ON registros_maestros(fecha_nacimiento);
    CREATE INDEX IF NOT EXISTS idx_maestros_mun    ON registros_maestros(municipio);
    CREATE INDEX IF NOT EXISTS idx_alertas_tipo    ON alertas_activas(tipo_alerta, estado);
    CREATE INDEX IF NOT EXISTS idx_audit_registro  ON audit_trail(registro_id, timestamp);
    """)

    conn.execute("INSERT OR IGNORE INTO db_meta (key, value) VALUES (?, ?)",
                 ("version", "0.7.0"))
    conn.execute("INSERT OR IGNORE INTO db_meta (key, value) VALUES (?, ?)",
                 ("description", "Matriz certificada de registros depurados — scanner_agent"))
    conn.execute("INSERT OR IGNORE INTO db_meta (key, value) VALUES (?, ?)",
                 ("created_at", _now_iso()))
    conn.commit()


def _source_label(record: dict) -> str:
    """Determina la fuente de un registro (CEE, RD, CESCO, etc.)."""
    return record.get("source_db", record.get("fuente", "DESCONOCIDO")).upper()


def write_confirmed_finding(
    db_path: Path,
    finding: dict,
    review: dict,
    accion_tomada: str,
    auditado_por: str,
    notas: str,
) -> dict:
    """
    Escribe un hallazgo confirmado a matriz_certificada.db.

    Parámetros:
        db_path       : Ruta a matriz_certificada.db
        finding       : Dict del hallazgo desde el reporte JSON
        review        : Dict del review_log para este hallazgo
        accion_tomada : Código de acción (ej. 'BAJA_DEFUNCION')
        auditado_por  : Nombre/rol del auditor
        notas         : Notas adicionales del auditor

    Retorna:
        dict con 'registros_escritos', 'alerta_id', 'timestamp'
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    flag        = finding.get("flag", "UNKNOWN")
    confidence  = finding.get("confidence", 0.0)
    description = finding.get("description", "")
    records     = finding.get("records", [])
    edited      = review.get("edited_records", {})
    deleted     = review.get("deleted_records", [])
    audit_date  = _now_iso()
    audit_status = _STATUS_MAP.get(accion_tomada, "verified")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        registro_ids = []

        for j, raw in enumerate(records):
            if j in deleted:
                logger.debug("Registro %d omitido (marcado para eliminación)", j)
                continue

            # Aplicar ediciones del auditor
            rec = dict(raw)
            if str(j) in edited:
                rec.update(edited[str(j)])

            # Clasificar fuente
            fuente = _source_label(rec)
            fuente_cee   = rec.get("num_elector")   if fuente == "CEE"   else None
            fuente_cesco = rec.get("num_elector")   if fuente == "CESCO" else None
            fuente_rd    = rec.get("num_elector")   if fuente == "RD"    else None

            # Nombre canónico
            nombre = rec.get("nombre") or ""
            ap     = rec.get("apellido_paterno") or ""
            am     = rec.get("apellido_materno") or ""
            nombre_completo = rec.get("nombre_completo") or " ".join(
                filter(None, [nombre, ap, am])
            )

            cur.execute("""
                INSERT INTO registros_maestros
                    (nombre, apellido_paterno, apellido_materno, nombre_completo,
                     fecha_nacimiento, edad, genero,
                     direccion, municipio, telefono, tipo_telefono,
                     seguro_social, audit_status, audit_date,
                     auditado_por, notas_revision, accion_tomada, flag_origen,
                     fuente_cee, fuente_cesco, fuente_rd)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                nombre, ap, am, nombre_completo,
                rec.get("fecha_nacimiento"), rec.get("edad"), rec.get("genero"),
                rec.get("direccion"), rec.get("municipio"),
                rec.get("telefono"), rec.get("tipo_telefono"),
                rec.get("seguro_social"),
                audit_status, audit_date,
                auditado_por, notas, accion_tomada, flag,
                fuente_cee, fuente_cesco, fuente_rd,
            ))
            reg_id = cur.lastrowid
            registro_ids.append(reg_id)

            # Fuente verificada
            cur.execute("""
                INSERT INTO fuentes_verificadas
                    (registro_id, fuente_nombre, fuente_id, fecha_verificacion, resultado)
                VALUES (?,?,?,?,?)
            """, (reg_id, fuente, rec.get("num_elector"), audit_date, "confirmado"))

            # Audit trail por registro
            cur.execute("""
                INSERT INTO audit_trail
                    (registro_id, accion, flag_origen, accion_tomada,
                     usuario, timestamp, notas, datos_despues)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                reg_id, "confirmed", flag, accion_tomada,
                auditado_por, audit_date, notas,
                json.dumps(rec, ensure_ascii=False, default=str),
            ))

        # Registrar la alerta como resuelta
        cur.execute("""
            INSERT INTO alertas_activas
                (tipo_alerta, confianza, registros_ids, descripcion,
                 estado, fecha_creacion, fecha_resolucion, notas_resolucion)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            flag, confidence,
            json.dumps(registro_ids),
            description,
            "resuelto", audit_date, audit_date,
            f"{accion_tomada} — {notas}" if notas else accion_tomada,
        ))
        alerta_id = cur.lastrowid

        conn.commit()
        logger.info(
            "Matriz certificada: %d registros escritos | flag=%s accion=%s auditor=%s",
            len(registro_ids), flag, accion_tomada, auditado_por,
        )
        return {
            "registros_escritos": len(registro_ids),
            "registro_ids":       registro_ids,
            "alerta_id":          alerta_id,
            "timestamp":          audit_date,
        }

    except Exception:
        conn.rollback()
        logger.exception("Error escribiendo a matriz_certificada.db")
        raise
    finally:
        conn.close()


def get_stats(db_path: Path) -> dict:
    """Retorna estadísticas resumidas de matriz_certificada.db."""
    db_path = Path(db_path)
    if not db_path.exists():
        return {"total": 0, "por_estado": {}, "por_accion": {}, "alertas_resueltas": 0}

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        total = cur.execute("SELECT COUNT(*) FROM registros_maestros").fetchone()[0]

        por_estado = {
            row[0]: row[1]
            for row in cur.execute(
                "SELECT audit_status, COUNT(*) FROM registros_maestros GROUP BY audit_status"
            ).fetchall()
        }
        por_accion = {
            row[0]: row[1]
            for row in cur.execute(
                "SELECT accion_tomada, COUNT(*) FROM registros_maestros "
                "WHERE accion_tomada IS NOT NULL GROUP BY accion_tomada"
            ).fetchall()
        }
        alertas = cur.execute(
            "SELECT COUNT(*) FROM alertas_activas WHERE estado='resuelto'"
        ).fetchone()[0]

        return {
            "total":              total,
            "por_estado":         por_estado,
            "por_accion":         por_accion,
            "alertas_resueltas":  alertas,
        }
    finally:
        conn.close()
