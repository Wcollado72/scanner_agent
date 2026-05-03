"""
scanner_agent.models  v0.8.0

Modelos de datos del sistema de auditoria de gobierno.

Dominios soportados:
  ELECTORAL  - padron CEE, RD defunciones/nacimientos, CESCO Real ID
  NOMINA     - nomina de empleados, agencias de gobierno
  CONTRATOS  - pagos a contratistas, facturas, ordenes de compra
  PENSIONES  - sistema de retiro (ELA, municipios, maestros)
  SALUD      - Medicaid/Medicare, proveedores de salud
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# -- Dominios de auditoria --------------------------------------------------

class AuditDomain:
    """Constantes de dominio. Usar como source_domain en RowRecord."""
    ELECTORAL  = "ELECTORAL"   # CEE, RD, CESCO
    NOMINA     = "NOMINA"      # Nomina agencias gobierno
    CONTRATOS  = "CONTRATOS"   # Pagos contratistas / ordenes compra
    PENSIONES  = "PENSIONES"   # Sistema retiro
    SALUD      = "SALUD"       # Medicaid / proveedores salud


# -- Flags por dominio -------------------------------------------------------

class AuditFlag:
    """
    Catalogo centralizado de flags por dominio.
    Todos los modulos de deteccion deben usar estas constantes.
    """
    # Electoral (intra-base)
    EXACT_DUPLICATE          = "EXACT_DUPLICATE"
    NEAR_DUPLICATE           = "NEAR_DUPLICATE"
    POSSIBLE_DECEASED        = "POSSIBLE_DECEASED"
    ANOMALY                  = "ANOMALY"
    ADDRESS_CLUSTER          = "ADDRESS_CLUSTER"

    # Electoral (cross-DB)
    CONFIRMED_DECEASED       = "CONFIRMED_DECEASED"
    NAME_MISMATCH_CROSS_DB   = "NAME_MISMATCH_CROSS_DB"
    SSN_CROSS_DB_CONFLICT    = "SSN_CROSS_DB_CONFLICT"

    # Nomina
    MULTI_AGENCY_EMPLOYEE    = "MULTI_AGENCY_EMPLOYEE"
    DECEASED_EMPLOYEE        = "DECEASED_EMPLOYEE"
    SALARY_ANOMALY           = "SALARY_ANOMALY"
    GHOST_EMPLOYEE           = "GHOST_EMPLOYEE"
    PENSION_ACTIVE_CONFLICT  = "PENSION_ACTIVE_CONFLICT"

    # Contratos / Pagos
    DUPLICATE_PAYMENT        = "DUPLICATE_PAYMENT"
    DECEASED_PAYEE           = "DECEASED_PAYEE"
    VENDOR_SSN_CONFLICT      = "VENDOR_SSN_CONFLICT"
    CONTRACT_ANOMALY         = "CONTRACT_ANOMALY"

    # Pensiones
    DECEASED_PENSIONER       = "DECEASED_PENSIONER"
    DUPLICATE_PENSION        = "DUPLICATE_PENSION"
    PENSION_AGE_ANOMALY      = "PENSION_AGE_ANOMALY"

    # Salud
    DUPLICATE_CLAIM          = "DUPLICATE_CLAIM"
    DECEASED_PROVIDER        = "DECEASED_PROVIDER"
    PROVIDER_CREDENTIAL_GAP  = "PROVIDER_CREDENTIAL_GAP"


# -- File scanning models ---------------------------------------------------

@dataclass(slots=True)
class FileRecord:
    path: Path
    source: str
    size_bytes: int
    modified_time: float
    sha256: Optional[str] = None
    extension: str = ""
    code_fingerprint: Optional[str] = None


@dataclass(slots=True)
class DuplicateGroup:
    strategy: str
    confidence: float
    records: list[FileRecord] = field(default_factory=list)


# -- Database / record scanning models --------------------------------------

@dataclass(slots=True)
class RowRecord:
    """
    Representa una fila de cualquier base de datos del gobierno.

    Campos de identidad (todos los dominios):
        row_id, source_table, source_db, source_domain
        nombre, apellido_paterno, apellido_materno
        fecha_nacimiento, edad, genero, seguro_social

    Campos electorales:
        direccion, municipio, telefono, tipo_telefono

    Campos de nomina / empleo (NOMINA):
        agencia, num_empleado, puesto, salario,
        fecha_inicio_empleo, fecha_fin_empleo

    Campos financieros (CONTRATOS / PENSIONES / SALUD):
        num_transaccion, monto, fecha_transaccion,
        agencia_pagadora, num_contrato, descripcion_pago
    """

    # -- Identidad universal ------------------------------------------------
    row_id: str                     # PK o ID unico en la tabla fuente
    source_table: str               # ej. "electores", "nomina_2024", "pagos"
    source_db: str                  # Alias de la base: "CEE", "NOMINA_HACIENDA"

    source_domain: str = AuditDomain.ELECTORAL   # Dominio de auditoria

    # -- Campos de persona (todos los dominios) -----------------------------
    nombre: Optional[str] = None
    apellido_paterno: Optional[str] = None
    apellido_materno: Optional[str] = None
    fecha_nacimiento: Optional[str] = None   # ISO-8601 YYYY-MM-DD
    edad: Optional[int] = None
    genero: Optional[str] = None
    seguro_social: Optional[str] = None      # Parcial: ***-**-XXXX

    # -- Electoral ----------------------------------------------------------
    direccion: Optional[str] = None
    municipio: Optional[str] = None
    telefono: Optional[str] = None           # "787-555-1234"
    tipo_telefono: Optional[str] = None      # "Movil" | "Residencia" | "Trabajo"

    # -- Nomina / Empleo (NOMINA) -------------------------------------------
    agencia: Optional[str] = None            # Nombre de la agencia/departamento
    num_empleado: Optional[str] = None       # ID interno del empleado
    puesto: Optional[str] = None             # Titulo / clasificacion del puesto
    salario: Optional[float] = None          # Salario anual o por periodo
    fecha_inicio_empleo: Optional[str] = None
    fecha_fin_empleo: Optional[str] = None   # None si activo

    # -- Financiero / Pagos (CONTRATOS, PENSIONES, SALUD) ------------------
    num_transaccion: Optional[str] = None    # ID unico del pago/transaccion
    monto: Optional[float] = None            # Monto en USD
    fecha_transaccion: Optional[str] = None  # ISO-8601 YYYY-MM-DD
    agencia_pagadora: Optional[str] = None   # Agencia que emitio el pago
    num_contrato: Optional[str] = None       # Numero de contrato asociado
    descripcion_pago: Optional[str] = None   # Descripcion de la transaccion

    # -- Computed -----------------------------------------------------------
    nombre_normalizado: Optional[str] = None  # Mayusculas, sin tildes
    row_hash: Optional[str] = None            # SHA-256 de campos canonicos

    # -- Raw data -----------------------------------------------------------
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def nombre_completo(self) -> str:
        parts = filter(None, [self.nombre, self.apellido_paterno, self.apellido_materno])
        return " ".join(parts)

    @property
    def is_possibly_deceased(self) -> bool:
        """Flags records donde edad > 100 o DOB implica edad extrema."""
        if self.edad is not None and self.edad > 100:
            return True
        return False

    @property
    def has_anomalous_age(self) -> bool:
        """Flags records con DOB en el futuro o edad > 115 (implausible)."""
        if self.edad is not None and (self.edad < 0 or self.edad > 115):
            return True
        return False


@dataclass(slots=True)
class RecordMatchGroup:
    """
    Grupo de RowRecords que el motor de deteccion considera sospechosos.
    Usado en todos los dominios (electoral, nomina, contratos, etc.).
    """

    strategy: str     # "exact_hash" | "name_dob_fuzzy" | "ssn_cross" | "multi_agency" | ...
    confidence: float # 0.0 - 1.0
    flag: str         # Usar constantes de AuditFlag
    records: list[RowRecord] = field(default_factory=list)
    notes: str = ""   # Explicacion legible para el auditor
    domain: str = AuditDomain.ELECTORAL  # Dominio de este hallazgo
