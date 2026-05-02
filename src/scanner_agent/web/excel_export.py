"""
scanner_agent.web.excel_export
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Generates a formatted Excel audit report from scanner_agent findings + review log.
Sheets: Resumen | Todos los Hallazgos | Confirmados | Citaciones | Historial
"""
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, GradientFill
)
from openpyxl.utils import get_column_letter

# ── Color palette ──────────────────────────────────────────────────────────
C_DARK       = "1a1a2e"
C_HEADER_BG  = "16213e"
C_WHITE      = "FFFFFF"
C_CONFIRMED  = "d1e7dd"
C_CONFIRMED_T= "0f5132"
C_REJECTED   = "cfe2ff"
C_REJECTED_T = "084298"
C_PENDING    = "f8f9fa"
C_PENDING_T  = "6c757d"
C_EXACT      = "f8d7da"
C_NEAR       = "fff3cd"
C_DECEASED   = "d1ecf1"
C_ANOMALY    = "e2e3e5"
C_ACCENT     = "0d6efd"
C_CITATION   = "fff8e1"

FLAG_COLORS = {
    "EXACT_DUPLICATE":   (C_EXACT,   "721c24"),
    "NEAR_DUPLICATE":    (C_NEAR,    "664d03"),
    "POSSIBLE_DECEASED": (C_DECEASED,"0c5460"),
    "ANOMALY":           (C_ANOMALY, "41464b"),
}
FLAG_LABELS = {
    "EXACT_DUPLICATE":   "Duplicado Exacto",
    "NEAR_DUPLICATE":    "Duplicado Aprox.",
    "POSSIBLE_DECEASED": "Pos. Fallecido",
    "ANOMALY":           "Anomalia",
}
STATUS_COLORS = {
    "confirmed": (C_CONFIRMED,  C_CONFIRMED_T),
    "rejected":  (C_REJECTED,   C_REJECTED_T),
    "pending":   (C_PENDING,    C_PENDING_T),
}

_thin = Side(style="thin", color="DEE2E6")
_border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


def _hdr(ws, row, col, value, bg=C_HEADER_BG, fg=C_WHITE, bold=True, wrap=False):
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = Font(name="Arial", bold=bold, color=fg, size=9)
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=wrap)
    cell.border = _border
    return cell


def _cell(ws, row, col, value, bg=None, fg="1a1a2e", bold=False, align="left", wrap=False):
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = Font(name="Arial", color=fg, size=9, bold=bold)
    if bg:
        cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    cell.border = _border
    return cell


def _set_col_widths(ws, widths: list[int]):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _freeze(ws, cell="A2"):
    ws.freeze_panes = cell


# ── Sheet builders ─────────────────────────────────────────────────────────

def _sheet_resumen(wb, findings, reviews, meta):
    ws = wb.create_sheet("Resumen")
    ws.sheet_view.showGridLines = False

    # Title block
    ws.merge_cells("A1:H1")
    t = ws.cell(row=1, column=1, value="Scanner Agent — Reporte de Auditoria CEE")
    t.font = Font(name="Arial", bold=True, size=14, color=C_WHITE)
    t.fill = PatternFill("solid", fgColor=C_DARK)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:H2")
    sub = ws.cell(row=2, column=1,
                  value=f"Fuente: {meta.get('source','—')}  |  Tabla: {meta.get('table','—')}  |  "
                        f"Escaneado: {meta.get('scan_timestamp','')[:19].replace('T',' ')} UTC  |  "
                        f"Registros: {meta.get('total_rows_scanned','—')}")
    sub.font = Font(name="Arial", size=9, color="6c757d")
    sub.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 16

    # Summary stats header
    row = 4
    for col, lbl in enumerate(["Categoria", "Total", "Confirmados", "Rechazados", "Pendientes"], 1):
        _hdr(ws, row, col, lbl)
    ws.row_dimensions[row].height = 18

    # Per-flag counts
    from collections import Counter
    flag_counts = Counter(f["flag"] for f in findings)
    row = 5
    for flag, (bg, fg) in FLAG_COLORS.items():
        _cell(ws, row, 1, FLAG_LABELS[flag], bg=bg, fg=fg, bold=True)
        indices = [i for i, f in enumerate(findings) if f["flag"] == flag]
        total_n = len(indices)
        conf_n  = sum(1 for i in indices if reviews.get(str(i), {}).get("status") == "confirmed")
        rej_n   = sum(1 for i in indices if reviews.get(str(i), {}).get("status") == "rejected")
        pend_n  = total_n - conf_n - rej_n
        for col, val in enumerate([total_n, conf_n, rej_n, pend_n], 2):
            _cell(ws, row, col, val, bg=bg, fg=fg, align="center")
        row += 1

    # Totals row
    _cell(ws, row, 1, "TOTAL", bold=True, bg="f0f0f0", fg=C_DARK, align="center")
    for col in range(2, 6):
        ws.cell(row=row, column=col).value = f"=SUM({get_column_letter(col)}5:{get_column_letter(col)}{row-1})"
        ws.cell(row=row, column=col).font = Font(name="Arial", bold=True, size=9)
        ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor="f0f0f0")
        ws.cell(row=row, column=col).alignment = Alignment(horizontal="center")
        ws.cell(row=row, column=col).border = _border

    _set_col_widths(ws, [26, 10, 12, 12, 12])
    _freeze(ws, "A3")


def _sheet_findings(wb, findings, reviews, title="Todos los Hallazgos", filter_status=None):
    ws = wb.create_sheet(title)
    ws.sheet_view.showGridLines = False

    headers = ["#", "Tipo", "Flag", "Confianza", "Estado", "ID Reg. 1",
               "Nombre", "Fecha Nac.", "Municipio", "Telefono", "Tipo Tel.",
               "ID Reg. 2", "Nombre 2", "Fecha Nac. 2", "Municipio 2", "Telefono 2", "Notas"]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 1, col, h)
    ws.row_dimensions[1].height = 18

    row = 2
    for i, f in enumerate(findings):
        rev    = reviews.get(str(i), {})
        status = rev.get("status", "pending")
        if filter_status and status != filter_status:
            continue

        records = f.get("records", [])
        # Merge any edits
        edited = rev.get("edited_records", {})
        merged = []
        for j, r in enumerate(records):
            m = dict(r)
            if str(j) in edited:
                m.update(edited[str(j)])
            merged.append(m)

        r0 = merged[0] if len(merged) > 0 else {}
        r1 = merged[1] if len(merged) > 1 else {}

        flag_bg, flag_fg = FLAG_COLORS.get(f["flag"], ("FFFFFF", "000000"))
        st_bg, st_fg     = STATUS_COLORS.get(status, (C_PENDING, C_PENDING_T))
        conf_pct         = f"{f['confidence']:.0%}"

        vals = [
            i + 1,
            FLAG_LABELS.get(f["flag"], f["flag"]),
            f["flag"],
            conf_pct,
            status.upper(),
            r0.get("row_id", ""),
            r0.get("nombre_completo", ""),
            r0.get("fecha_nacimiento", ""),
            r0.get("municipio", ""),
            r0.get("telefono", ""),
            r0.get("tipo_telefono", ""),
            r1.get("row_id", ""),
            r1.get("nombre_completo", ""),
            r1.get("fecha_nacimiento", ""),
            r1.get("municipio", ""),
            r1.get("telefono", ""),
            f.get("notes", ""),
        ]
        for col, v in enumerate(vals, 1):
            if col in (2, 3):
                _cell(ws, row, col, v, bg=flag_bg, fg=flag_fg, bold=(col == 2))
            elif col == 5:
                _cell(ws, row, col, v, bg=st_bg, fg=st_fg, bold=True, align="center")
            elif col == 4:
                _cell(ws, row, col, v, align="center")
            else:
                _cell(ws, row, col, v)
        ws.row_dimensions[row].height = 15
        row += 1

    _set_col_widths(ws, [5, 16, 18, 9, 11, 12, 28, 11, 14, 14, 9, 12, 28, 11, 14, 14, 30])
    _freeze(ws, "A2")


def _sheet_citations(wb, findings, reviews):
    ws = wb.create_sheet("Citaciones")
    ws.sheet_view.showGridLines = False

    headers = ["Hallazgo #", "Flag", "Nombre", "Telefono", "Fecha Cita",
               "Hora", "Lugar", "Motivo", "Documentos Requeridos", "Registrado por", "Fecha Registro"]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 1, col, h)
    ws.row_dimensions[1].height = 18

    row = 2
    for i, f in enumerate(findings):
        rev      = reviews.get(str(i), {})
        contacts = rev.get("contacts", [])
        for c in contacts:
            if c.get("type") != "citacion":
                continue
            flag_bg, flag_fg = FLAG_COLORS.get(f["flag"], ("FFFFFF", "000000"))
            vals = [
                i + 1,
                FLAG_LABELS.get(f["flag"], f["flag"]),
                c.get("nombre", ""),
                c.get("telefono", ""),
                c.get("fecha_cita", ""),
                c.get("hora_cita", ""),
                c.get("lugar", ""),
                c.get("motivo", ""),
                c.get("documentos", ""),
                c.get("role", ""),
                c.get("timestamp", "")[:19].replace("T", " "),
            ]
            for col, v in enumerate(vals, 1):
                bg = C_CITATION if col > 2 else flag_bg
                fg = flag_fg if col == 2 else C_DARK
                _cell(ws, row, col, v, bg=bg, fg=fg, bold=(col == 2), wrap=(col == 9))
            ws.row_dimensions[row].height = 15
            row += 1

    _set_col_widths(ws, [10, 16, 28, 14, 11, 7, 22, 30, 40, 12, 18])
    _freeze(ws, "A2")


def _sheet_history(wb, findings, reviews):
    ws = wb.create_sheet("Historial")
    ws.sheet_view.showGridLines = False

    headers = ["Hallazgo #", "Flag", "Accion", "Rol", "Timestamp", "Notas"]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 1, col, h)
    ws.row_dimensions[1].height = 18

    row = 2
    for i, f in enumerate(findings):
        rev     = reviews.get(str(i), {})
        history = rev.get("action_history", [])
        flag_bg, flag_fg = FLAG_COLORS.get(f["flag"], ("FFFFFF", "000000"))
        for entry in history:
            vals = [
                i + 1,
                FLAG_LABELS.get(f["flag"], f["flag"]),
                entry.get("action", "").upper(),
                entry.get("role", ""),
                entry.get("timestamp", "")[:19].replace("T", " "),
                entry.get("notes", ""),
            ]
            for col, v in enumerate(vals, 1):
                bg = flag_bg if col == 2 else None
                fg = flag_fg if col == 2 else C_DARK
                _cell(ws, row, col, v, bg=bg, fg=fg, bold=(col == 2), wrap=(col == 6))
            ws.row_dimensions[row].height = 15
            row += 1

    _set_col_widths(ws, [10, 16, 14, 12, 18, 50])
    _freeze(ws, "A2")



def _sheet_municipios(wb, findings, reviews):
    ws = wb.create_sheet("Municipios")
    ws.sheet_view.showGridLines = False

    # Build per-municipio stats from findings
    from collections import defaultdict
    stats = defaultdict(lambda: {"EXACT_DUPLICATE":0,"NEAR_DUPLICATE":0,
                                  "POSSIBLE_DECEASED":0,"ANOMALY":0})
    reviews_by_idx = reviews or {}
    for i, f in enumerate(findings):
        muns = {r.get("municipio","") for r in f.get("records",[]) if r.get("municipio")}
        for mun in muns:
            stats[mun][f["flag"]] = stats[mun].get(f["flag"],0) + 1

    headers = ["Municipio","Exactos","Near-Dup","Pos. Fallecido","Anomalias","Total"]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 1, col, h)
    ws.row_dimensions[1].height = 18

    sorted_muns = sorted(stats.items(), key=lambda x: -(
        x[1]["EXACT_DUPLICATE"]+x[1]["NEAR_DUPLICATE"]+x[1]["POSSIBLE_DECEASED"]+x[1]["ANOMALY"]
    ))

    for row, (mun, cnts) in enumerate(sorted_muns, 2):
        total = cnts["EXACT_DUPLICATE"]+cnts["NEAR_DUPLICATE"]+cnts["POSSIBLE_DECEASED"]+cnts["ANOMALY"]
        # Intensity: darker background for more findings
        intensity = min(255, 255 - int(total / max(1, len(sorted_muns)) * 80))
        row_bg = f"{intensity:02X}{intensity:02X}FF" if total > 0 else "FFFFFF"
        vals = [mun, cnts["EXACT_DUPLICATE"], cnts["NEAR_DUPLICATE"],
                cnts["POSSIBLE_DECEASED"], cnts["ANOMALY"], total]
        for col, v in enumerate(vals, 1):
            bold = col == 1 or col == 6
            align = "left" if col == 1 else "center"
            _cell(ws, row, col, v, bg=row_bg if col > 1 else None, bold=bold, align=align)
        ws.row_dimensions[row].height = 15

    # Totals
    last = len(sorted_muns) + 1
    _cell(ws, last+1, 1, "TOTAL", bold=True, bg="f0f0f0", align="center")
    for col in range(2, 7):
        ws.cell(row=last+1, column=col).value = f"=SUM({get_column_letter(col)}2:{get_column_letter(col)}{last})"
        ws.cell(row=last+1, column=col).font = Font(name="Arial", bold=True, size=9)
        ws.cell(row=last+1, column=col).fill = PatternFill("solid", fgColor="f0f0f0")
        ws.cell(row=last+1, column=col).alignment = Alignment(horizontal="center")
        ws.cell(row=last+1, column=col).border = _border

    _set_col_widths(ws, [22, 9, 10, 15, 11, 8])
    _freeze(ws, "A2")

# ── Public entry point ─────────────────────────────────────────────────────

def build_excel_report(findings: list, reviews: dict, meta: dict, out_path: Path) -> None:
    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    _sheet_resumen(wb, findings, reviews, meta)
    _sheet_findings(wb, findings, reviews, title="Todos los Hallazgos")
    _sheet_findings(wb, findings, reviews, title="Confirmados", filter_status="confirmed")
    _sheet_citations(wb, findings, reviews)
    _sheet_history(wb, findings, reviews)
    _sheet_municipios(wb, findings, reviews)

    wb.save(str(out_path))
