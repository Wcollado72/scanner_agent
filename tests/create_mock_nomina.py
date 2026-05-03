"""
tests/create_mock_nomina.py  v0.9.0
Genera tests/data/mock_nomina.db — nómina sintética del gobierno de PR.

Casos de prueba embebidos:
  MULTI_AGENCY_EMPLOYEE  : 5 empleados con el mismo SSN en 2 agencias distintas
  DECEASED_EMPLOYEE      : 3 empleados cuyo SSN aparece como fallecido en mock_rd.db
  SALARY_ANOMALY         : 5 registros con salario > 3x la mediana de su categoría
"""

import os
import random
import sqlite3
from pathlib import Path

random.seed(42)

AGENCIAS = [
    "DEPARTAMENTO DE EDUCACION",
    "DEPARTAMENTO DE SALUD",
    "DEPARTAMENTO DE HACIENDA",
    "DEPARTAMENTO DE JUSTICIA",
    "POLICIA DE PUERTO RICO",
    "DEPARTAMENTO DE TRANSPORTACION",
    "DEPARTAMENTO DE FAMILIA",
    "AUTORIDAD DE ENERGIA ELECTRICA",
    "DEPARTAMENTO DE AGRICULTURA",
    "OFICINA DE GERENCIA Y PRESUPUESTO",
]

CATEGORIAS = {
    "MAESTRO":           (38_000,  62_000),
    "ENFERMERO":         (45_000,  75_000),
    "AUDITOR":           (42_000,  68_000),
    "AGENTE_POLICIAL":   (32_000,  55_000),
    "INGENIERO":         (48_000,  80_000),
    "ADMINISTRATIVO":    (28_000,  45_000),
    "DIRECTOR":          (65_000,  95_000),
    "TECNICO":           (30_000,  52_000),
    "TRABAJADOR_SOCIAL": (35_000,  58_000),
    "ANALISTA":          (40_000,  65_000),
}

NOMBRES_M = ["Carlos","Jose","Luis","Miguel","Angel","Pedro","Juan","Roberto",
             "Fernando","Eduardo","Ricardo","Diego","Hector","Ramon","Pablo"]
NOMBRES_F = ["Maria","Carmen","Ana","Rosa","Diana","Iris","Wanda","Luz",
             "Gloria","Ivette","Marisol","Xiomara","Leticia","Sandra","Patricia"]
APELLIDOS  = ["Rivera","Martinez","Rodriguez","Garcia","Lopez","Hernandez",
              "Perez","Torres","Colon","Reyes","Cruz","Morales","Ortiz","Diaz",
              "Ramos","Vargas","Pagan","Nieves","Burgos","Medina","Santiago"]
MUNICIPIOS = ["San Juan","Bayamon","Carolina","Ponce","Caguas","Arecibo",
              "Guaynabo","Humacao","Mayaguez","Fajardo","Vega Baja","Toa Baja"]

# SSNs de fallecidos reales en mock_rd.db — usados para DECEASED_EMPLOYEE
DECEASED_SSNS = [
    ("***-**-6918", "Jesus",  "Hernandez", "Cruz"),
    ("***-**-7008", "Pedro",  "Pagan",     "Rivera"),
    ("***-**-4967", "Juan",   "Diaz",      "Correa"),
]


def load_rd_ssns(rd_path: str) -> set:
    """Carga todos los SSNs del RD para evitar colisiones accidentales."""
    if not Path(rd_path).exists():
        return set()
    try:
        conn = sqlite3.connect(rd_path)
        rows = conn.execute(
            "SELECT seguro_social FROM defunciones WHERE seguro_social IS NOT NULL"
        ).fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def rnd_ssn(used: set) -> str:
    while True:
        s = f"***-**-{random.randint(1000, 9999)}"
        if s not in used:
            used.add(s)
            return s


def rnd_fecha(year_from=1960, year_to=1995) -> str:
    return f"{random.randint(year_from,year_to)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"


def rnd_inicio() -> str:
    return f"{random.randint(2005,2022)}-{random.randint(1,12):02d}-01"


def gen_clean(n: int, reserved: set) -> tuple:
    used = set(reserved)  # incluye todos los SSNs del RD + DECEASED
    rows = []
    for i in range(n):
        g   = random.choice(["M", "F"])
        cat = random.choice(list(CATEGORIAS.keys()))
        lo, hi = CATEGORIAS[cat]
        rows.append({
            "emp_id":           f"EMP-{i+1:04d}",
            "nombre":           random.choice(NOMBRES_M if g=="M" else NOMBRES_F),
            "apellido_paterno": random.choice(APELLIDOS),
            "apellido_materno": random.choice(APELLIDOS),
            "seguro_social":    rnd_ssn(used),
            "agencia":          random.choice(AGENCIAS),
            "puesto":           cat.replace("_", " "),
            "categoria":        cat,
            "salario":          round(random.uniform(lo, hi), 2),
            "fecha_inicio":     rnd_inicio(),
            "fecha_fin":        None,
            "municipio":        random.choice(MUNICIPIOS),
            "fecha_nacimiento": rnd_fecha(),
            "genero":           g,
        })
    return rows, used


def gen_multi_agency(used: set, start_id=281):
    rows = []
    for i in range(5):
        ssn = rnd_ssn(used)
        g   = random.choice(["M", "F"])
        cat = random.choice(list(CATEGORIAS.keys()))
        lo, hi = CATEGORIAS[cat]
        dob = rnd_fecha()
        for j, ag in enumerate(random.sample(AGENCIAS, 2)):
            rows.append({
                "emp_id":           f"MULTI-{start_id+i:03d}-{'AB'[j]}",
                "nombre":           random.choice(NOMBRES_M if g=="M" else NOMBRES_F),
                "apellido_paterno": random.choice(APELLIDOS),
                "apellido_materno": random.choice(APELLIDOS),
                "seguro_social":    ssn,
                "agencia":          ag,
                "puesto":           cat.replace("_", " "),
                "categoria":        cat,
                "salario":          round(random.uniform(lo, hi), 2),
                "fecha_inicio":     rnd_inicio(),
                "fecha_fin":        None,
                "municipio":        random.choice(MUNICIPIOS),
                "fecha_nacimiento": dob,
                "genero":           g,
            })
    return rows


def gen_deceased_employees(start_id=286):
    rows = []
    for i, (ssn, nombre, ap1, ap2) in enumerate(DECEASED_SSNS):
        cat = random.choice(list(CATEGORIAS.keys()))
        lo, hi = CATEGORIAS[cat]
        rows.append({
            "emp_id":           f"DEC-{start_id+i:03d}",
            "nombre":           nombre,
            "apellido_paterno": ap1,
            "apellido_materno": ap2,
            "seguro_social":    ssn,
            "agencia":          random.choice(AGENCIAS),
            "puesto":           cat.replace("_", " "),
            "categoria":        cat,
            "salario":          round(random.uniform(lo, hi), 2),
            "fecha_inicio":     rnd_inicio(),
            "fecha_fin":        None,
            "municipio":        random.choice(MUNICIPIOS),
            "fecha_nacimiento": rnd_fecha(),
            "genero":           "M",
        })
    return rows


def gen_salary_anomalies(used: set, start_id=289):
    anomaly_cats = ["MAESTRO","ADMINISTRATIVO","AGENTE_POLICIAL",
                    "TRABAJADOR_SOCIAL","TECNICO"]
    rows = []
    for i, cat in enumerate(anomaly_cats):
        lo, hi = CATEGORIAS[cat]
        median = (lo + hi) / 2
        rows.append({
            "emp_id":           f"SAL-{start_id+i:03d}",
            "nombre":           random.choice(NOMBRES_M),
            "apellido_paterno": random.choice(APELLIDOS),
            "apellido_materno": random.choice(APELLIDOS),
            "seguro_social":    rnd_ssn(used),
            "agencia":          random.choice(AGENCIAS),
            "puesto":           cat.replace("_", " "),
            "categoria":        cat,
            "salario":          round(median * random.uniform(3.2, 5.0), 2),
            "fecha_inicio":     rnd_inicio(),
            "fecha_fin":        None,
            "municipio":        random.choice(MUNICIPIOS),
            "fecha_nacimiento": rnd_fecha(),
            "genero":           "M",
        })
    return rows


def write_db(rows, path):
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE nomina_empleados (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_id           TEXT UNIQUE NOT NULL,
        nombre           TEXT NOT NULL,
        apellido_paterno TEXT NOT NULL,
        apellido_materno TEXT,
        seguro_social    TEXT,
        agencia          TEXT NOT NULL,
        puesto           TEXT,
        categoria        TEXT,
        salario          REAL,
        fecha_inicio     TEXT,
        fecha_fin        TEXT,
        municipio        TEXT,
        fecha_nacimiento TEXT,
        genero           TEXT
    );
    CREATE INDEX idx_nom_ssn     ON nomina_empleados(seguro_social);
    CREATE INDEX idx_nom_agencia ON nomina_empleados(agencia);
    CREATE INDEX idx_nom_cat     ON nomina_empleados(categoria);
    """)
    conn.executemany("""
        INSERT INTO nomina_empleados
          (emp_id,nombre,apellido_paterno,apellido_materno,seguro_social,
           agencia,puesto,categoria,salario,fecha_inicio,fecha_fin,
           municipio,fecha_nacimiento,genero)
        VALUES
          (:emp_id,:nombre,:apellido_paterno,:apellido_materno,:seguro_social,
           :agencia,:puesto,:categoria,:salario,:fecha_inicio,:fecha_fin,
           :municipio,:fecha_nacimiento,:genero)
    """, rows)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM nomina_empleados").fetchone()[0]
    conn.close()
    return n


if __name__ == "__main__":
    script_dir = Path(__file__).parent
    out        = script_dir / "data" / "mock_nomina.db"
    rd_path    = script_dir / "data" / "mock_rd.db"
    out.parent.mkdir(parents=True, exist_ok=True)

    # Reservar SSNs del RD + los 3 DECEASED_SSNS explícitos
    rd_ssns  = load_rd_ssns(str(rd_path))
    reserved = rd_ssns | {ssn for ssn, *_ in DECEASED_SSNS}
    print(f"SSNs reservados del RD: {len(rd_ssns)}")

    clean_rows, used = gen_clean(280, reserved)
    all_rows = (
        clean_rows
        + gen_multi_agency(used, 281)
        + gen_deceased_employees(286)
        + gen_salary_anomalies(used, 289)
    )

    n = write_db(all_rows, str(out))
    print(f"mock_nomina.db → {n} empleados")
    print(f"  Limpios:           280")
    print(f"  MULTI_AGENCY:       10  (5 SSNs × 2 agencias)")
    print(f"  DECEASED_EMPLOYEE:   3  (SSNs de mock_rd.db)")
    print(f"  SALARY_ANOMALY:      5")
    print(f"  Total:             {n}")
