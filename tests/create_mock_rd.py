"""
create_mock_rd.py — Genera base de datos sintética del Registro Demográfico de PR.

Simula:
  - Defunciones registradas (2015–2025)
  - Nacimientos registrados (2015–2025)

Incluye casos de prueba específicos para cruce con CEE:
  Caso 2: José Manuel Pérez Colón — fallecido 2022, activo en CEE
  Caso 3: SSN ***-**-4471 aparece en defunciones (Francisco Nieves Santos)
"""

import os, random, sqlite3
from datetime import date, timedelta

random.seed(99)
DB_PATH = "tests/data/mock_rd.db"

NOMBRES_M = ["Jose", "Carlos", "Luis", "Miguel", "Juan", "Angel", "Pedro",
             "Rafael", "Roberto", "Hector", "Fernando", "Ricardo", "Francisco",
             "Alejandro", "Eduardo", "Victor", "Jesus", "Manuel", "Jorge"]
NOMBRES_F = ["Maria", "Carmen", "Ana", "Rosa", "Isabel", "Marta", "Diana",
             "Laura", "Gladys", "Iris", "Norma", "Sonia", "Gloria", "Evelyn",
             "Miriam", "Wanda", "Zuleika", "Marisol", "Yaritza", "Keishla"]
APELLIDOS = ["Rivera", "Rodriguez", "Garcia", "Torres", "Lopez", "Perez",
             "Sanchez", "Ortiz", "Morales", "Cruz", "Reyes", "Flores",
             "Vazquez", "Diaz", "Colon", "Martinez", "Delgado", "Ramos",
             "Serrano", "Santiago", "Medina", "Hernandez", "Rosario",
             "Acevedo", "Pagan", "Nieves", "Correa", "Figueroa", "Burgos"]
MUNICIPIOS = ["San Juan", "Bayamon", "Carolina", "Ponce", "Caguas",
              "Guaynabo", "Arecibo", "Toa Baja", "Mayaguez", "Humacao",
              "Aguadilla", "Fajardo", "Coamo", "Vega Baja", "Rio Grande"]
CAUSAS = [
    "Enfermedad cardiovascular", "Diabetes mellitus", "Neoplasia maligna",
    "Enfermedad cerebrovascular", "Neumonia", "Alzheimer", "Insuficiencia renal",
    "COVID-19", "Accidente de transito", "Causa natural", "Insuficiencia cardiaca",
    "Enfermedad pulmonar obstructiva", "Cirrosis hepatica"
]

def rnd_date(y1, y2):
    s = date(y1, 1, 1); e = date(y2, 12, 31)
    return s + timedelta(days=random.randint(0, (e-s).days))

def fake_ssn():
    return f"***-**-{random.randint(1000,9999)}"

idx = 1
def defuncion_id():
    global idx; v = f"RD-DEF-{idx:07d}"; idx += 1; return v

defunciones = []
nacimientos = []

# ── 200 defunciones aleatorias ─────────────────────────────────────────────
for _ in range(200):
    g = random.choice(["M","F"])
    nombre = random.choice(NOMBRES_M if g=="M" else NOMBRES_F)
    dob    = rnd_date(1920, 1970)
    death  = rnd_date(2015, 2025)
    defunciones.append({
        "cert_num":         defuncion_id(),
        "nombre":           nombre,
        "apellido_paterno": random.choice(APELLIDOS),
        "apellido_materno": random.choice(APELLIDOS),
        "fecha_nacimiento": dob.isoformat(),
        "fecha_defuncion":  death.isoformat(),
        "sexo":             g,
        "municipio_res":    random.choice(MUNICIPIOS),
        "municipio_muerte": random.choice(MUNICIPIOS),
        "causa_muerte":     random.choice(CAUSAS),
        "seguro_social":    fake_ssn() if random.random() < 0.60 else None,
        "edad_muerte":      (death - dob).days // 365,
    })

# ── CASO 2: José Manuel Pérez Colón — activo en CEE, fallecido 2022 ────────
# DOB debe coincidir con el que pondremos en mock_cee.db para este caso
defunciones.append({
    "cert_num":         "RD-DEF-CASO2-001",
    "nombre":           "Jose",
    "apellido_paterno": "Perez",
    "apellido_materno": "Colon",
    "fecha_nacimiento": "1940-07-22",
    "fecha_defuncion":  "2022-11-15",
    "sexo":             "M",
    "municipio_res":    "Caguas",
    "municipio_muerte": "Caguas",
    "causa_muerte":     "Insuficiencia cardiaca",
    "seguro_social":    "***-**-7291",
    "edad_muerte":      82,
})

# ── CASO 3: Francisco Nieves Santos — SSN ***-**-4471 en defunciones ────────
defunciones.append({
    "cert_num":         "RD-DEF-CASO3-001",
    "nombre":           "Francisco",
    "apellido_paterno": "Nieves",
    "apellido_materno": "Santos",
    "fecha_nacimiento": "1958-03-10",
    "fecha_defuncion":  "2019-06-04",
    "sexo":             "M",
    "municipio_res":    "Bayamon",
    "municipio_muerte": "Bayamon",
    "causa_muerte":     "Neoplasia maligna",
    "seguro_social":    "***-**-4471",
    "edad_muerte":      61,
})

# ── 150 nacimientos aleatorios ─────────────────────────────────────────────
nac_idx = 1
for _ in range(150):
    g = random.choice(["M","F"])
    birth = rnd_date(2015, 2025)
    nacimientos.append({
        "cert_num":             f"RD-NAC-{nac_idx:07d}",
        "nombre":               random.choice(NOMBRES_M if g=="M" else NOMBRES_F),
        "apellido_paterno":     random.choice(APELLIDOS),
        "apellido_materno":     random.choice(APELLIDOS),
        "fecha_nacimiento":     birth.isoformat(),
        "sexo":                 g,
        "municipio_nacimiento": random.choice(MUNICIPIOS),
        "peso_gramos":          random.randint(2200, 4200),
        "semanas_gestacion":    random.randint(36, 42),
    })
    nac_idx += 1

random.shuffle(defunciones)
random.shuffle(nacimientos)

# ── Crear SQLite ───────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

cur.executescript("""
DROP TABLE IF EXISTS defunciones;
CREATE TABLE defunciones (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cert_num         TEXT NOT NULL UNIQUE,
    nombre           TEXT,
    apellido_paterno TEXT,
    apellido_materno TEXT,
    fecha_nacimiento TEXT,
    fecha_defuncion  TEXT NOT NULL,
    sexo             TEXT,
    municipio_res    TEXT,
    municipio_muerte TEXT,
    causa_muerte     TEXT,
    seguro_social    TEXT,
    edad_muerte      INTEGER,
    created_at       TEXT DEFAULT (datetime('now'))
);

DROP TABLE IF EXISTS nacimientos;
CREATE TABLE nacimientos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    cert_num             TEXT NOT NULL UNIQUE,
    nombre               TEXT,
    apellido_paterno     TEXT,
    apellido_materno     TEXT,
    fecha_nacimiento     TEXT,
    sexo                 TEXT,
    municipio_nacimiento TEXT,
    peso_gramos          INTEGER,
    semanas_gestacion    INTEGER,
    created_at           TEXT DEFAULT (datetime('now'))
);
""")

for d in defunciones:
    cur.execute("""INSERT OR IGNORE INTO defunciones
        (cert_num,nombre,apellido_paterno,apellido_materno,fecha_nacimiento,
         fecha_defuncion,sexo,municipio_res,municipio_muerte,causa_muerte,
         seguro_social,edad_muerte)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d["cert_num"],d["nombre"],d["apellido_paterno"],d["apellido_materno"],
         d["fecha_nacimiento"],d["fecha_defuncion"],d["sexo"],d["municipio_res"],
         d["municipio_muerte"],d["causa_muerte"],d["seguro_social"],d["edad_muerte"]))

for n in nacimientos:
    cur.execute("""INSERT OR IGNORE INTO nacimientos
        (cert_num,nombre,apellido_paterno,apellido_materno,fecha_nacimiento,
         sexo,municipio_nacimiento,peso_gramos,semanas_gestacion)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (n["cert_num"],n["nombre"],n["apellido_paterno"],n["apellido_materno"],
         n["fecha_nacimiento"],n["sexo"],n["municipio_nacimiento"],
         n["peso_gramos"],n["semanas_gestacion"]))

conn.commit()
d_count = cur.execute("SELECT COUNT(*) FROM defunciones").fetchone()[0]
n_count = cur.execute("SELECT COUNT(*) FROM nacimientos").fetchone()[0]
conn.close()

print(f"mock_rd.db creada: {DB_PATH}")
print(f"  Defunciones : {d_count}  (incluye Caso 2 y Caso 3)")
print(f"  Nacimientos : {n_count}")
