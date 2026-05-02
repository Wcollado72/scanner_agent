"""
create_mock_cesco.py — Genera base de datos sintética del CESCO de PR.

En Puerto Rico, el CESCO emite DOS tipos de documentos bajo el Real ID Act:

  Real ID - Licencia    Licencia de conducir CONFORME al Real ID Act federal.
                        Tiene la estrella dorada. Permite conducir Y es
                        aceptada para vuelos domesticos y edificios federales.

  Real ID - Tarjeta ID  Tarjeta de identificacion NO conductora, conforme
                        al Real ID Act. Tiene la estrella dorada. Solo sirve
                        como identificacion (no conduce).

Ambas son Real ID. La diferencia es si otorgan privilegio de conducir o no.

Incluye casos de prueba para cruce con CEE:
  Caso 1: Maria Elena Torres RIERA — apellido typo vs Torres RIVERA en CEE
  Caso 3: Tres registros con SSN ***-**-4471 (conflicto cross-DB)
"""

import os, random, sqlite3
from datetime import date, timedelta

random.seed(77)
DB_PATH = "tests/data/mock_cesco.db"

NOMBRES_M = ["Jose","Carlos","Luis","Miguel","Juan","Angel","Pedro","Rafael",
             "Roberto","Hector","Fernando","Ricardo","Alejandro","Eduardo",
             "Victor","Jesus","Manuel","Francisco","Jorge","Antonio"]
NOMBRES_F = ["Maria","Carmen","Ana","Rosa","Isabel","Marta","Diana","Laura",
             "Gladys","Iris","Norma","Sonia","Gloria","Evelyn","Miriam",
             "Wanda","Zuleika","Marisol","Yaritza","Keishla"]
APELLIDOS = ["Rivera","Rodriguez","Garcia","Torres","Lopez","Perez","Sanchez",
             "Ortiz","Morales","Cruz","Reyes","Flores","Vazquez","Diaz","Colon",
             "Martinez","Delgado","Ramos","Serrano","Santiago","Medina",
             "Hernandez","Rosario","Acevedo","Pagan","Nieves","Correa","Burgos"]
MUNICIPIOS = ["San Juan","Bayamon","Carolina","Ponce","Caguas","Guaynabo",
              "Arecibo","Toa Baja","Mayaguez","Humacao","Aguadilla","Fajardo",
              "Coamo","Vega Baja","Rio Grande","Trujillo Alto"]
CALLES    = ["Calle Loiza","Ave. Ponce de Leon","Calle Sol","Urb. Santa Rosa",
             "Calle Fortaleza","Ave. Fernandez Juncos","Calle Isabel",
             "Urb. Las Vegas","Calle Betances","Ave. De Diego"]

# Tipos de licencia de conducir (solo aplican a Real ID - Licencia)
TIPOS_LIC_CONDUCIR = ["Regular","Comercial","Motocicleta","CDL-A","CDL-B"]
RESTRICCIONES_LIC  = ["Ninguna","Lentes","Solo automatico","Lentes y automatico"]

# Tipos de documento CESCO — ambos son Real ID
# Distribucion estimada en PR post-implementacion plena (2025)
TIPO_DOC_LICENCIA  = "Real ID - Licencia"      # conduce + ID federal
TIPO_DOC_TARJETA   = "Real ID - Tarjeta ID"    # solo ID federal, no conduce
PESOS_DOC  = [0.72, 0.28]   # mayoria tiene licencia

def rnd_date(y1, y2):
    s = date(y1,1,1); e = date(y2,12,31)
    return s + timedelta(days=random.randint(0,(e-s).days))

def fake_ssn():   return f"***-**-{random.randint(1000,9999)}"
def fake_rid(i):  return f"RID-{str(i).zfill(7)}"
def fake_phone():
    if random.random() < 0.8:
        p = random.choice(["787","939"])
        n = random.randint(2000000,9999999)
        return f"{p}-{str(n)[:3]}-{str(n)[3:7]}"
    return None
def fake_address():
    return f"{random.choice(CALLES)} #{random.randint(1,999)}"
def doc_id(i):    return f"CESCO{str(i).zfill(8)}"

records = []
idx = 1

# ── 400 documentos aleatorios ──────────────────────────────────────────────
for _ in range(400):
    g        = random.choice(["M","F"])
    dob      = rnd_date(1940, 2003)
    exp      = rnd_date(2025, 2030)
    tipo_doc = random.choices(
        [TIPO_DOC_LICENCIA, TIPO_DOC_TARJETA], weights=PESOS_DOC
    )[0]

    if tipo_doc == TIPO_DOC_LICENCIA:
        tipo_lic = random.choice(TIPOS_LIC_CONDUCIR)
        restricc = random.choice(RESTRICCIONES_LIC)
        puntos   = random.randint(0, 8)
    else:
        tipo_lic = "No aplica"       # Tarjeta ID no tiene tipo de licencia
        restricc = "No aplica"
        puntos   = 0                 # No acumula puntos de demerito

    records.append({
        "doc_num":          doc_id(idx),
        "num_real_id":      fake_rid(idx),     # Todos tienen numero Real ID
        "nombre":           random.choice(NOMBRES_M if g=="M" else NOMBRES_F),
        "apellido_paterno": random.choice(APELLIDOS),
        "apellido_materno": random.choice(APELLIDOS),
        "fecha_nacimiento": dob.isoformat(),
        "genero":           g,
        "direccion":        fake_address(),
        "municipio":        random.choice(MUNICIPIOS),
        "seguro_social":    fake_ssn() if random.random()<0.80 else None,
        "telefono":         fake_phone(),
        "tipo_documento":   tipo_doc,
        "tipo_licencia":    tipo_lic,
        "fecha_vencimiento":exp.isoformat(),
        "restricciones":    restricc,
        "puntos_demerito":  puntos,
        "activo":           1 if random.random()<0.90 else 0,
    })
    idx += 1

# ── CASO 1: Maria Elena Torres RIERA — typo vs Torres RIVERA en CEE ────────
records.append({
    "doc_num":          "CESCO-CASO1-001",
    "num_real_id":      "RID-9900001",
    "nombre":           "Maria Elena",
    "apellido_paterno": "Torres",
    "apellido_materno": "Riera",              # apellido materno erroneo (Rivera→Riera)
    "fecha_nacimiento": "1965-03-14",         # mismo DOB que en CEE
    "genero":           "F",
    "direccion":        "Calle Loiza #412",   # misma direccion que en CEE
    "municipio":        "San Juan",
    "seguro_social":    "***-**-8831",        # SSN diferente al de CEE (inconsistencia)
    "telefono":         "787-555-1234",
    "tipo_documento":   TIPO_DOC_LICENCIA,
    "tipo_licencia":    "Regular",
    "fecha_vencimiento":"2027-03-14",
    "restricciones":    "Lentes",
    "puntos_demerito":  0,
    "activo":           1,
})
idx += 1

# ── CASO 3: Tres registros con SSN ***-**-4471 (conflicto cross-DB) ─────────
SSN_CONFLICTO = "***-**-4471"
casos3 = [
    ("Pedro",   "Castro",  "Mendez", "1972-08-15", "Bayamon",  TIPO_DOC_LICENCIA),
    ("Luis",    "Vargas",  "Reyes",  "1985-11-30", "Carolina", TIPO_DOC_TARJETA),
    ("Roberto", "Nieves",  "Cruz",   "1968-04-22", "Ponce",    TIPO_DOC_LICENCIA),
]
for i, (nom, ap, am, dob, mun, tipo) in enumerate(casos3, 1):
    records.append({
        "doc_num":          f"CESCO-CASO3-00{i}",
        "num_real_id":      fake_rid(9900100 + i),
        "nombre":           nom,
        "apellido_paterno": ap,
        "apellido_materno": am,
        "fecha_nacimiento": dob,
        "genero":           "M",
        "direccion":        fake_address(),
        "municipio":        mun,
        "seguro_social":    SSN_CONFLICTO,
        "telefono":         fake_phone(),
        "tipo_documento":   tipo,
        "tipo_licencia":    "Regular" if tipo == TIPO_DOC_LICENCIA else "No aplica",
        "fecha_vencimiento":"2028-01-01",
        "restricciones":    "Ninguna" if tipo == TIPO_DOC_LICENCIA else "No aplica",
        "puntos_demerito":  random.randint(0, 3),
        "activo":           1,
    })
    idx += 1

random.shuffle(records)

# ── Crear SQLite ───────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

cur.executescript("""
DROP TABLE IF EXISTS documentos;
CREATE TABLE documentos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_num          TEXT NOT NULL UNIQUE,   -- numero interno CESCO
    num_real_id      TEXT NOT NULL,          -- numero Real ID federal (RID-XXXXXXX)
    nombre           TEXT,
    apellido_paterno TEXT,
    apellido_materno TEXT,
    fecha_nacimiento TEXT,
    genero           TEXT,
    direccion        TEXT,
    municipio        TEXT,
    seguro_social    TEXT,
    telefono         TEXT,
    tipo_documento   TEXT NOT NULL,
        -- 'Real ID - Licencia'   : conduce + identificacion federal
        -- 'Real ID - Tarjeta ID' : solo identificacion federal, no conduce
    tipo_licencia    TEXT,   -- Regular | Comercial | Motocicleta | CDL-A | CDL-B | No aplica
    fecha_vencimiento TEXT,
    restricciones    TEXT,
    puntos_demerito  INTEGER DEFAULT 0,
    activo           INTEGER DEFAULT 1,
    created_at       TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_doc_ssn ON documentos(seguro_social);
CREATE INDEX IF NOT EXISTS idx_doc_rid ON documentos(num_real_id);
CREATE INDEX IF NOT EXISTS idx_doc_dob ON documentos(fecha_nacimiento);
""")

for r in records:
    cur.execute("""
        INSERT OR IGNORE INTO documentos
        (doc_num,num_real_id,nombre,apellido_paterno,apellido_materno,
         fecha_nacimiento,genero,direccion,municipio,seguro_social,telefono,
         tipo_documento,tipo_licencia,fecha_vencimiento,restricciones,
         puntos_demerito,activo)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (r["doc_num"],r["num_real_id"],r["nombre"],r["apellido_paterno"],
         r["apellido_materno"],r["fecha_nacimiento"],r["genero"],r["direccion"],
         r["municipio"],r["seguro_social"],r["telefono"],r["tipo_documento"],
         r["tipo_licencia"],r["fecha_vencimiento"],r["restricciones"],
         r["puntos_demerito"],r["activo"]))

conn.commit()
count = cur.execute("SELECT COUNT(*) FROM documentos").fetchone()[0]
tipos = cur.execute(
    "SELECT tipo_documento, COUNT(*) FROM documentos GROUP BY tipo_documento ORDER BY 2 DESC"
).fetchall()
conn.close()

print(f"mock_cesco.db creada: {DB_PATH}")
print(f"  Total documentos    : {count}")
print(f"  Tipos de documento:")
for t, n in tipos:
    print(f"    {t:<28}: {n}")
print(f"  Caso 1 : Maria Elena Torres Riera (apellido typo vs CEE)")
print(f"  Caso 3 : 3 docs con SSN {SSN_CONFLICTO} (conflicto cross-DB)")
