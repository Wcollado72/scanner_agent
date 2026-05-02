"""
scanner_agent.web.app  v0.4.0
Web review dashboard for scanner_agent audit reports.

Launch via CLI:
    scanner-agent --scanner serve --report reports/audit_cee.json
"""
import datetime
import json
import logging
import os
from functools import wraps
from pathlib import Path

from flask import (
    Flask, flash, redirect, render_template,
    request, session, url_for, send_file,
)

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "scanner-dev-secret-change-me")

REVIEWER_PASSWORD = os.environ.get("REVIEWER_PASSWORD", "")
ADMIN_PASSWORD    = os.environ.get("ADMIN_PASSWORD", "")

FLAG_META = {
    "EXACT_DUPLICATE":          ("danger",    "Duplicado Exacto"),
    "NEAR_DUPLICATE":           ("warning",   "Duplicado Aproximado"),
    "POSSIBLE_DECEASED":        ("info",      "Posiblemente Fallecido"),
    "ANOMALY":                  ("secondary", "Anomalia"),
    "ADDRESS_CLUSTER":          ("dark",      "Concentracion de Direccion"),
    "CONFIRMED_DECEASED":       ("danger",    "Fallecido Confirmado"),
    "NAME_MISMATCH_CROSS_DB":   ("warning",   "Inconsistencia de Nombre"),
    "SSN_CROSS_DB_CONFLICT":    ("danger",    "Conflicto SSN Multi-Base"),
}

# Flags that require human follow-up (call / citation)
CONTACT_FLAGS = {"NEAR_DUPLICATE", "POSSIBLE_DECEASED", "ANOMALY", "ADDRESS_CLUSTER", "NAME_MISMATCH_CROSS_DB", "SSN_CROSS_DB_CONFLICT"}

# Default documents required per flag type
DEFAULT_DOCS = {
    "NEAR_DUPLICATE":          "Identificacion con foto vigente, Certificado de nacimiento, Comprobante de direccion",
    "POSSIBLE_DECEASED":       "Certificado de nacimiento, Identificacion con foto vigente",
    "ANOMALY":                 "Identificacion con foto vigente, Certificado de nacimiento",
    "ADDRESS_CLUSTER":         "Comprobante de residencia vigente, Identificacion con foto vigente, Contrato de arrendamiento o escritura",
    "CONFIRMED_DECEASED":      "Certificado de defuncion, Registro Demografico, Acta de defuncion",
    "NAME_MISMATCH_CROSS_DB":  "Real ID vigente, Certificado de nacimiento, Documento oficial con nombre correcto",
    "SSN_CROSS_DB_CONFLICT":   "Real ID vigente, Tarjeta de Seguro Social original, Certificado de nacimiento",
}

_report: dict = {}
_review_log_path: Path | None = None
_review_log: dict = {}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _save_review_log() -> None:
    if _review_log_path is None:
        return
    _review_log["meta"]["last_updated"] = _now_iso()
    _review_log_path.write_text(
        json.dumps(_review_log, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def init_app(report_path: Path, review_log_path: Path) -> None:
    global _report, _review_log_path, _review_log
    _report = json.loads(report_path.read_text(encoding="utf-8"))
    _review_log_path = review_log_path
    if review_log_path.exists():
        _review_log = json.loads(review_log_path.read_text(encoding="utf-8"))
    else:
        _review_log = {
            "meta": {
                "report_file":  str(report_path),
                "created_at":   _now_iso(),
                "last_updated": _now_iso(),
            },
            "reviews": {},
        }
        _save_review_log()
    global REVIEWER_PASSWORD, ADMIN_PASSWORD
    REVIEWER_PASSWORD = os.environ.get("REVIEWER_PASSWORD", REVIEWER_PASSWORD)
    ADMIN_PASSWORD    = os.environ.get("ADMIN_PASSWORD",    ADMIN_PASSWORD)
    app.secret_key    = os.environ.get("FLASK_SECRET_KEY",  app.secret_key)
    logger.info("Dashboard ready: %d findings loaded.", len(_report.get("findings", [])))


# ── Auth decorators ────────────────────────────────────────────────────────

def require_login(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "role" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Esta accion requiere permisos de Administrador.", "danger")
            return redirect(request.referrer or url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


# ── Routes: auth ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("dashboard") if "role" in session else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        pwd = request.form.get("password", "").strip()
        if ADMIN_PASSWORD and pwd == ADMIN_PASSWORD:
            session["role"] = "admin"
            session["role_label"] = "Administrador"
            return redirect(url_for("dashboard"))
        elif REVIEWER_PASSWORD and pwd == REVIEWER_PASSWORD:
            session["role"] = "revisor"
            session["role_label"] = "Revisor"
            return redirect(url_for("dashboard"))
        else:
            error = "Contrasena incorrecta. Intenta de nuevo."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    flash("Sesion cerrada correctamente.", "success")
    return redirect(url_for("login"))


# ── Routes: dashboard ──────────────────────────────────────────────────────

@app.route("/dashboard")
@require_login
def dashboard():
    findings = _report.get("findings", [])
    reviews  = _review_log.get("reviews", {})
    flag_filter   = request.args.get("flag",   "")
    status_filter = request.args.get("status", "")

    rows = []
    for i, f in enumerate(findings):
        rev    = reviews.get(str(i), {})
        status = rev.get("status", "pending")
        if flag_filter   and f["flag"]  != flag_filter:  continue
        if status_filter and status     != status_filter: continue
        color, label = FLAG_META.get(f["flag"], ("secondary", f["flag"]))
        names = " / ".join(r.get("nombre_completo", "") for r in f["records"][:2])
        contact_count = len(rev.get("contacts", []))
        rows.append({
            "idx":           i,
            "flag":          f["flag"],
            "flag_color":    color,
            "flag_label":    label,
            "strategy":      f["strategy"],
            "confidence":    f"{f['confidence']:.0%}",
            "names":         names[:80],
            "status":        status,
            "record_count":  len(f["records"]),
            "contact_count": contact_count,
        })

    total = len(findings)
    counts = {
        "total":     total,
        "pending":   sum(1 for i in range(total) if reviews.get(str(i), {}).get("status", "pending") == "pending"),
        "confirmed": sum(1 for i in range(total) if reviews.get(str(i), {}).get("status", "pending") == "confirmed"),
        "rejected":  sum(1 for i in range(total) if reviews.get(str(i), {}).get("status", "pending") == "rejected"),
    }
    return render_template(
        "dashboard.html",
        rows=rows, counts=counts, meta=_report.get("meta", {}),
        flag_filter=flag_filter, status_filter=status_filter,
        FLAG_META=FLAG_META,
    )


# ── Routes: finding detail ─────────────────────────────────────────────────

@app.route("/finding/<int:idx>")
@require_login
def finding(idx):
    findings = _report.get("findings", [])
    if not (0 <= idx < len(findings)):
        flash("Hallazgo no encontrado.", "danger")
        return redirect(url_for("dashboard"))
    f    = findings[idx]
    rev  = _review_log.get("reviews", {}).get(str(idx), {})
    color, label = FLAG_META.get(f["flag"], ("secondary", f["flag"]))
    edited  = rev.get("edited_records", {})
    deleted = rev.get("deleted_records", [])
    records = []
    for j, r in enumerate(f["records"]):
        merged = dict(r)
        if str(j) in edited:
            merged.update(edited[str(j)])
        merged["_deleted"] = j in deleted
        records.append(merged)
    return render_template(
        "finding.html",
        idx=idx, finding=f, records=records,
        flag_color=color, flag_label=label,
        status=rev.get("status", "pending"),
        history=rev.get("action_history", []),
        contacts=rev.get("contacts", []),
        role=session.get("role"),
        total=len(findings),
        needs_contact=f["flag"] in CONTACT_FLAGS,
        default_docs=DEFAULT_DOCS.get(f["flag"], ""),
    )


# ── Routes: review actions ─────────────────────────────────────────────────

@app.route("/finding/<int:idx>/confirm", methods=["POST"])
@require_login
def confirm_finding(idx):
    _apply_status(idx, "confirmed", "confirm", request.form.get("notes", "").strip())
    flash("Hallazgo confirmado como duplicado.", "success")
    return redirect(url_for("finding", idx=idx))


@app.route("/finding/<int:idx>/reject", methods=["POST"])
@require_login
def reject_finding(idx):
    _apply_status(idx, "rejected", "reject", request.form.get("notes", "").strip())
    flash("Hallazgo rechazado (falso positivo).", "info")
    return redirect(url_for("finding", idx=idx))


@app.route("/finding/<int:idx>/edit/<int:rec_idx>", methods=["POST"])
@require_login
@require_admin
def edit_record(idx, rec_idx):
    fields = {k: request.form.get(k, "").strip() for k in
              ("nombre_completo", "telefono", "tipo_telefono", "direccion", "municipio")}
    fields = {k: v for k, v in fields.items() if v}
    rev = _review_log["reviews"].setdefault(
        str(idx), {"status": "pending", "action_history": [], "edited_records": {}})
    rev.setdefault("edited_records", {})[str(rec_idx)] = fields
    _log_action(idx, "edit", notes=f"Registro {rec_idx} editado", extra={"changes": fields})
    _save_review_log()
    flash(f"Registro {rec_idx + 1} actualizado.", "success")
    return redirect(url_for("finding", idx=idx))


@app.route("/finding/<int:idx>/delete/<int:rec_idx>", methods=["POST"])
@require_login
@require_admin
def delete_record(idx, rec_idx):
    notes = request.form.get("notes", "").strip()
    rev = _review_log["reviews"].setdefault(
        str(idx), {"status": "pending", "action_history": []})
    deleted = rev.setdefault("deleted_records", [])
    if rec_idx not in deleted:
        deleted.append(rec_idx)
    _log_action(idx, "delete",
                notes=f"Registro {rec_idx} marcado para eliminacion. {notes}".strip())
    _save_review_log()
    flash(f"Registro {rec_idx + 1} marcado para eliminacion.", "warning")
    return redirect(url_for("finding", idx=idx))


# ── Routes: contact / citation ─────────────────────────────────────────────

@app.route("/finding/<int:idx>/contact", methods=["POST"])
@require_login
def log_contact(idx):
    """Register a call attempt for a finding."""
    findings = _report.get("findings", [])
    if not (0 <= idx < len(findings)):
        return redirect(url_for("dashboard"))

    entry = {
        "timestamp":   _now_iso(),
        "type":        "llamada",
        "resultado":   request.form.get("resultado", "").strip(),
        "notas":       request.form.get("notas", "").strip(),
        "role":        session.get("role", "unknown"),
        "record_idx":  request.form.get("record_idx", "0"),
    }
    rev = _review_log["reviews"].setdefault(
        str(idx), {"status": "pending", "action_history": [], "contacts": []})
    rev.setdefault("contacts", []).append(entry)
    _log_action(idx, "llamada", notes=f"{entry['resultado']} — {entry['notas']}")
    _save_review_log()
    flash(f"Llamada registrada: {entry['resultado']}.", "success")
    return redirect(url_for("finding", idx=idx))


@app.route("/finding/<int:idx>/citation", methods=["POST"])
@require_login
def log_citation(idx):
    """Create a citation/summons record for a finding."""
    findings = _report.get("findings", [])
    if not (0 <= idx < len(findings)):
        return redirect(url_for("dashboard"))

    f       = findings[idx]
    records = f.get("records", [])
    rec_idx = int(request.form.get("record_idx", 0))
    target  = records[rec_idx] if rec_idx < len(records) else {}

    entry = {
        "timestamp":            _now_iso(),
        "type":                 "citacion",
        "nombre":               target.get("nombre_completo", ""),
        "telefono":             target.get("telefono", ""),
        "fecha_cita":           request.form.get("fecha_cita", "").strip(),
        "hora_cita":            request.form.get("hora_cita", "").strip(),
        "lugar":                request.form.get("lugar", "").strip(),
        "motivo":               request.form.get("motivo", "").strip(),
        "documentos":           request.form.get("documentos", "").strip(),
        "role":                 session.get("role", "unknown"),
        "record_idx":           rec_idx,
    }
    rev = _review_log["reviews"].setdefault(
        str(idx), {"status": "pending", "action_history": [], "contacts": []})
    rev.setdefault("contacts", []).append(entry)
    _log_action(idx, "citacion",
                notes=f"Citacion para {entry['nombre']} el {entry['fecha_cita']} {entry['hora_cita']}")
    _save_review_log()
    flash(f"Citacion registrada para {entry['nombre'] or 'registro ' + str(rec_idx+1)}.", "success")
    return redirect(url_for("finding", idx=idx))


# ── Routes: Excel export ───────────────────────────────────────────────────

@app.route("/export")
@require_login
def export_excel():
    """Generate and serve a formatted Excel audit report."""
    try:
        from scanner_agent.web.excel_export import build_excel_report
        findings = _report.get("findings", [])
        reviews  = _review_log.get("reviews", {})
        meta     = _report.get("meta", {})
        out_path = _review_log_path.parent / "audit_export.xlsx" if _review_log_path else Path("audit_export.xlsx")
        build_excel_report(findings, reviews, meta, out_path)
        return send_file(str(out_path), as_attachment=True,
                         download_name="audit_export.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as exc:
        logger.error("Excel export failed: %s", exc)
        flash(f"Error al generar el reporte Excel: {exc}", "danger")
        return redirect(url_for("dashboard"))


# ── Helpers ────────────────────────────────────────────────────────────────

def _apply_status(idx: int, status: str, action: str, notes: str) -> None:
    rev = _review_log["reviews"].setdefault(
        str(idx), {"status": "pending", "action_history": []})
    rev["status"] = status
    _log_action(idx, action, notes=notes)
    _save_review_log()


def _log_action(idx: int, action: str, notes: str = "", extra: dict | None = None) -> None:
    rev = _review_log["reviews"].setdefault(
        str(idx), {"status": "pending", "action_history": []})
    entry: dict = {
        "timestamp": _now_iso(),
        "action":    action,
        "role":      session.get("role", "unknown"),
        "notes":     notes,
    }
    if extra:
        entry.update(extra)
    rev.setdefault("action_history", []).append(entry)
