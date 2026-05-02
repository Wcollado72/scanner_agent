"""
Genera una base de datos SQLite sintetica que imita la estructura
de la Comision Estatal de Elecciones (CEE) de Puerto Rico.

Incluye:
  - Registros limpios (personas unicas)
  - Duplicados exactos (mismos datos, diferente ID)
  - Near-duplicates (variaciones de nombre, DOB, direccion)
  - Registros de posibles fallecidos (nacidos antes de 1920)
  - Inconsistencias de datos tipicas

NO contiene datos reales. Todo es ficcion.
"""

import os
import random
import sqlite3
from datetime import date, timedelta

DB_PATH = "tests/data/mock_cee.db"

# ── Datos ficticios de Puerto Rico ─────────────────────────────────────────

NOMBRES_M = [
    "Jose", "Carlos", "Luis", "Miguel", "Juan", "Angel", "Pedro", "Rafael",
    "Roberto", "Hector", "Fernando", "Ricardo", "Alejandro", "Eduardo", "Victor",
    "Jesus", "Manuel", "Francisco", "Jorge", "Antonio", "Ramon", "Gilberto",
    "Orlando", "Efrain", "Nelson", "Edwin", "Ivan", "Wilberto", "Javier", "Alexis"
]
NOMBRES_F = [
    "Maria", "Carmen", "Ana", "Rosa", "Isabel", "Marta", "Diana", "Laura",
    "Gladys", "Iris", "Norma", "Sonia", "Gloria", "Evelyn", "Miriam",
    "Wanda", "Zuleika", "Marisol", "Yaritza", "Keishla", "Nayda", "Lydia",
    "Xiomara", "Awilda", "Damaris", "Noelia", "Lisette", "Brunilda", "Alicia", "Elena"
]
APELLIDOS = [
    "Rivera", "Rodriguez", "Garcia", "Torres", "Lopez", "Perez", "Sanchez",
    "Ortiz", "Morales", "Cruz", "Reyes", "Flores", "Vazquez", "Diaz", "Colon",
    "Martinez", "Delgado", "Ramos", "Serrano", "Santiago", "Medina", "Hernandez",
    "Rosario", "Acevedo", "Pagan", "Velez", "Correa", "Nunez", "Crespo", "Figueroa",
    "Alvarado", "Mendez", "Ocasio", "Vargas", "Camacho", "Resto", "Nieves",
    "Maldonado", "Aponte", "Quinones", "Montalvo", "Mercado", "Lugo", "Burgos"
]
CALLES = [
    "Calle Loiza", "Av. Ponce de Leon", "Calle Sol", "Calle Luna", "Calle Fortaleza",
    "Calle San Jose", "Urb. Santa Rosa", "Urb. Las Vegas", "Bo. Santurce",
    "Calle Marginal", "Av. Fernandez Juncos", "Calle Isabel", "Calle Comercio",
    "Urb. Floral Park", "Calle Betances", "Av. De Diego", "Calle Recinto Sur",
    "Urb. Hyde Park", "Calle Doncella", "Bo. Obrero", "Calle Canals"
]
MUNICIPIOS = [
    "San Juan", "Bayamon", "Carolina", "Ponce", "Caguas", "Guaynabo",
    "Arecibo", "Toa Baja", "Mayaguez", "Trujillo Alto", "Humacao",
    "Aguadilla", "Fajardo", "Coamo", "Vega Baja", "Isabela", "Rio Grande"
]
PREFIJOS_PR = ["787", "939"]
TIPO_TEL = ["Movil", "Residencia", "Trabajo"]

random.seed(42)


def fake_ssn():
    return f"***-**-{random.randint(1000, 9999)}"


def fake_phone():
    """Genera un numero de telefono de PR ficticio."""
    if random.random() < 0.70:   # 70% de los registros tienen telefono
        prefix = random.choice(PREFIJOS_PR)
        number = random.randint(2000000, 9999999)
        return f"{prefix}-{str(number)[:3]}-{str(number)[3:7]}", random.choice(TIPO_TEL)
    return None, None


def fake_address():
    calle = random.choice(CALLES)
    num = random.randint(1, 999)
    apt = f" Apt {random.randint(1, 20)}" if random.random() < 0.3 else ""
    return f"{calle} #{num}{apt}"


def random_dob(min_year=1930, max_year=2006):
    start = date(min_year, 1, 1)
    end   = date(max_year, 12, 31)
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def voter_id(i):
    return f"CEE{str(i).zfill(7)}"


def make_person(idx, nombre=None, apellido_p=None, apellido_m=None,
                dob=None, municipio=None, address=None, ssn=None,
                genero=None, telefono=None, tipo_telefono=None):
    if genero is None:
        genero = random.choice(["M", "F"])
    if nombre is None:
        nombre = random.choice(NOMBRES_M if genero == "M" else NOMBRES_F)
    if apellido_p is None:
        apellido_p = random.choice(APELLIDOS)
    if apellido_m is None:
        apellido_m = random.choice(APELLIDOS)
    if dob is None:
        dob = random_dob()
    if municipio is None:
        municipio = random.choice(MUNICIPIOS)
    if address is None:
        address = fake_address()
    if ssn is None:
        ssn = fake_ssn() if random.random() < 0.75 else None
    if telefono is None:
        telefono, tipo_telefono = fake_phone()

    edad = (date.today() - dob).days // 365

    return {
        "num_elector":      voter_id(idx),
        "nombre":           nombre,
        "apellido_paterno": apellido_p,
        "apellido_materno": apellido_m,
        "nombre_completo":  f"{nombre} {apellido_p} {apellido_m}",
        "fecha_nacimiento": dob.isoformat(),
        "edad":             edad,
        "genero":           genero,
        "direccion":        address,
        "municipio":        municipio,
        "seguro_social":    ssn,
        "telefono":         telefono,
        "tipo_telefono":    tipo_telefono,
        "activo":           1,
    }


# ── Generacion de registros ────────────────────────────────────────────────

records = []
idx = 1

# 1. Registros limpios (350 personas)
clean_persons = []
for _ in range(350):
    r = make_person(idx)
    records.append(r)
    clean_persons.append(r)
    idx += 1

# 2. Duplicados EXACTOS (50 pares)
exact_sources = random.sample(clean_persons, 50)
for src in exact_sources:
    dup = make_person(
        idx,
        nombre=src["nombre"], apellido_p=src["apellido_paterno"],
        apellido_m=src["apellido_materno"],
        dob=date.fromisoformat(src["fecha_nacimiento"]),
        municipio=src["municipio"], address=src["direccion"],
        ssn=src["seguro_social"], genero=src["genero"],
        telefono=src["telefono"], tipo_telefono=src["tipo_telefono"],
    )
    records.append(dup)
    idx += 1

# 3. Near-duplicates — variacion de nombre (30 casos)
def typo_name(name):
    variations = {
        "Jose": "Josse", "Maria": "Maira", "Angel": "Anjel",
        "Hector": "Hektor", "Victor": "Viktor", "Efrain": "Efraim",
        "Jesus": "Jessus", "Ramon": "Ramone", "Nayda": "Naida",
        "Zuleika": "Zuleica", "Brunilda": "Brunhilda",
    }
    return variations.get(name, name[:-1] if len(name) > 3 else name + "a")

near_sources = random.sample(clean_persons, 30)
for src in near_sources:
    near = make_person(
        idx,
        nombre=typo_name(src["nombre"]),
        apellido_p=src["apellido_paterno"],
        apellido_m=src["apellido_materno"],
        dob=date.fromisoformat(src["fecha_nacimiento"]),
        municipio=src["municipio"],
        address=fake_address(),
        ssn=src["seguro_social"], genero=src["genero"],
        telefono=src["telefono"], tipo_telefono=src["tipo_telefono"],
    )
    records.append(near)
    idx += 1

# 4. Near-duplicates — DOB con 1 ano de diferencia (20 casos)
dob_sources = random.sample(clean_persons, 20)
for src in dob_sources:
    orig_dob = date.fromisoformat(src["fecha_nacimiento"])
    new_dob  = orig_dob.replace(year=orig_dob.year + random.choice([-1, 1]))
    near = make_person(
        idx,
        nombre=src["nombre"], apellido_p=src["apellido_paterno"],
        apellido_m=src["apellido_materno"], dob=new_dob,
        municipio=src["municipio"], address=src["direccion"],
        ssn=src["seguro_social"], genero=src["genero"],
        telefono=src["telefono"], tipo_telefono=src["tipo_telefono"],
    )
    records.append(near)
    idx += 1

# 5. Posibles fallecidos — nacidos antes de 1920 (15 registros)
for _ in range(15):
    dob = random_dob(min_year=1905, max_year=1919)
    r = make_person(idx, dob=dob)
    records.append(r)
    idx += 1

# 6. Edad anomala — DOB en el futuro o hace 130+ anos (5 registros)
for dob in [date(1894, 3, 15), date(1887, 7, 22), date(1899, 11, 5),
            date(2030, 1, 1), date(2045, 6, 15)]:
    r = make_person(idx, dob=dob)
    records.append(r)
    idx += 1

# 7. Casos de prueba especificos para cruce cross-DB
# ─────────────────────────────────────────────────────────────────────────
# Caso 1 (CEE): Maria Elena Torres Rivera — licencia CESCO dice "Riera"
records.append(make_person(
    idx,
    nombre="Maria Elena", apellido_p="Torres", apellido_m="Rivera",
    dob=date(1965, 3, 14), municipio="San Juan",
    address="Calle Loiza #412", ssn="***-**-9922", genero="F",
    telefono="787-555-1234", tipo_telefono="Movil",
))
idx += 1

# Caso 2 (CEE): Jose Perez Colon — activo en CEE, fallecido en RD el 2022-11-15
records.append(make_person(
    idx,
    nombre="Jose", apellido_p="Perez", apellido_m="Colon",
    dob=date(1940, 7, 22), municipio="Caguas",
    address="Calle Luna #18", ssn="***-**-7291", genero="M",
))
idx += 1

# Caso 3 (CEE): Elector con SSN ***-**-4471 — mismo SSN en RD (fallecido) y 3 en CESCO
records.append(make_person(
    idx,
    nombre="Ana", apellido_p="Santos", apellido_m="Gomez",
    dob=date(1975, 5, 20), municipio="Bayamon",
    address="Calle Sol #10", ssn="***-**-4471", genero="F",
))
idx += 1

# 8. Address clusters - concentracion anomala en misma direccion
# ─────────────────────────────────────────────────────────────────────────
# Scenario A: 30 voters at one residential address (hard threshold = 20)
CLUSTER_ADDR_A = "Calle San Jorge #45"
CLUSTER_MUN_A  = "San Juan"
for _ in range(30):
    r = make_person(idx, address=CLUSTER_ADDR_A, municipio=CLUSTER_MUN_A)
    records.append(r)
    idx += 1

# Scenario B: 12 voters at a PO Box (always flagged, 0-threshold)
CLUSTER_ADDR_B = "PO Box 90210"
CLUSTER_MUN_B  = "Bayamon"
for _ in range(12):
    r = make_person(idx, address=CLUSTER_ADDR_B, municipio=CLUSTER_MUN_B)
    records.append(r)
    idx += 1

# Scenario C: 8 voters at a commercial Suite address (soft threshold = 2, hard = 5)
CLUSTER_ADDR_C = "Ave Ponce de Leon 1050 Suite 301"
CLUSTER_MUN_C  = "San Juan"
for _ in range(8):
    r = make_person(idx, address=CLUSTER_ADDR_C, municipio=CLUSTER_MUN_C)
    records.append(r)
    idx += 1

# Scenario D: 9 voters at an urbanizacion street (soft threshold = 10 — soft flag)
CLUSTER_ADDR_D = "Urb Santa Rosa Calle Orquidea #7"
CLUSTER_MUN_D  = "Caguas"
for _ in range(9):
    r = make_person(idx, address=CLUSTER_ADDR_D, municipio=CLUSTER_MUN_D)
    records.append(r)
    idx += 1

random.shuffle(records)

# ── Crear la base de datos ─────────────────────────────────────────────────

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

cur.execute("DROP TABLE IF EXISTS electores")
cur.execute("""
CREATE TABLE electores (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    num_elector      TEXT    NOT NULL UNIQUE,
    nombre           TEXT    NOT NULL,
    apellido_paterno TEXT    NOT NULL,
    apellido_materno TEXT,
    nombre_completo  TEXT,
    fecha_nacimiento TEXT,
    edad             INTEGER,
    genero           TEXT,
    direccion        TEXT,
    municipio        TEXT,
    seguro_social    TEXT,
    telefono         TEXT,
    tipo_telefono    TEXT,
    activo           INTEGER DEFAULT 1,
    created_at       TEXT    DEFAULT (datetime('now'))
)
""")

for r in records:
    cur.execute("""
        INSERT OR IGNORE INTO electores
        (num_elector, nombre, apellido_paterno, apellido_materno,
         nombre_completo, fecha_nacimiento, edad, genero,
         direccion, municipio, seguro_social,
         telefono, tipo_telefono, activo)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        r["num_elector"], r["nombre"], r["apellido_paterno"],
        r["apellido_materno"], r["nombre_completo"],
        r["fecha_nacimiento"], r["edad"], r["genero"],
        r["direccion"], r["municipio"], r["seguro_social"],
        r["telefono"], r["tipo_telefono"], r["activo"],
    ))

conn.commit()

count    = cur.execute("SELECT COUNT(*) FROM electores").fetchone()[0]
deceased = cur.execute("SELECT COUNT(*) FROM electores WHERE edad > 100").fetchone()[0]
w_phone  = cur.execute("SELECT COUNT(*) FROM electores WHERE telefono IS NOT NULL").fetchone()[0]
conn.close()

print(f"mock_cee.db creada: {DB_PATH}")
print(f"  Total registros         : {count}")
print(f"  Con telefono            : {w_phone}")
print(f"  Posibles fallecidos     : {deceased}")
print(f"  Incluye: exactos, near-dups, fallecidos, anomalias, clusters de direccion")
print(f"    - Cluster residencial  : 30 en Calle San Jorge #45, San Juan (hard)")
print(f"    - Cluster PO Box       : 12 en PO Box 90210, Bayamon (siempre)")
print(f"    - Cluster comercial    : 8 en Suite 301, San Juan (siempre)")
print(f"    - Cluster urbanizacion : 9 en Urb Santa Rosa, Caguas (soft)")
