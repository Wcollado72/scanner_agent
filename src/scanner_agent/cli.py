import argparse
import datetime
import logging
import sys
import time
from pathlib import Path

from scanner_agent.config import Settings
from scanner_agent.detection.duplicate_engine import group_exact_duplicates
from scanner_agent.detection.hash_engine import sha256_file
from scanner_agent.detection.record_matcher import run_full_match
from scanner_agent.logging_config import configure_logging
from scanner_agent.reporting.report_writer import (
    write_duplicates_report,
    write_inventory_report,
)
from scanner_agent.scanners.db_scanner import DbScanConfig, scan_db_table
from scanner_agent.scanners.local_scanner import scan_local_paths

logger = logging.getLogger(__name__)

CLI_VERSION = "0.9.0"

SUPPORTED_SCANNERS = ("local", "db", "cross-db", "pipeline", "nomina", "serve")

# ── Nomina field map ──────────────────────────────────────────────────────
NOMINA_FIELD_MAP = {
    "row_id":               "emp_id",
    "nombre":               "nombre",
    "apellido_paterno":     "apellido_paterno",
    "apellido_materno":     "apellido_materno",
    "fecha_nacimiento":     "fecha_nacimiento",
    "genero":               "genero",
    "municipio":            "municipio",
    "seguro_social":        "seguro_social",
    "agencia":              "agencia",
    "puesto":               "puesto",
    "salario":              "salario",
    "fecha_inicio_empleo":  "fecha_inicio",
    "fecha_fin_empleo":     "fecha_fin",
}

# ── Rich import (optional — degrades gracefully if not installed) ──────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich import box as rich_box
    _RICH = True
    _console = Console()
except ImportError:
    _RICH = False
    _console = None


# ── Argument parser ────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scanner-agent",
        description="Safe-first file intelligence and duplicate detection engine.",
    )
    parser.add_argument(
        "--scanner",
        choices=SUPPORTED_SCANNERS,
        default="local",
        help="Scanner backend to use. Default: local.",
    )
    # Local scanner options
    parser.add_argument(
        "--path",
        action="append",
        help="[local] Path to scan. Can be provided multiple times.",
    )
    # DB scanner options
    parser.add_argument(
        "--db",
        default="",
        help="[db] SQLite connection string, e.g. sqlite:///path/to/db.sqlite",
    )
    parser.add_argument(
        "--table",
        default="electores",
        help="[db] Table to scan. Default: electores.",
    )
    parser.add_argument(
        "--source-alias",
        default="",
        help="[db] Human label for the source (e.g. CEE, CESCO). Used in reports.",
    )
    # Cross-DB scanner options
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        metavar="ALIAS:connection_string:table",
        help=(
            "[cross-db] Fuente de datos en formato ALIAS:connection_string:table. "
            "Puede repetirse para cada base de datos. "
            "Ejemplos: "
            "  CEE:sqlite:///tests/data/mock_cee.db:electores "
            "  RD:sqlite:///tests/data/mock_rd.db:defunciones "
            "  CESCO:sqlite:///tests/data/mock_cesco.db:documentos "
            "  CEE:mssql+pyodbc://server/AuditDB?driver=ODBC+Driver+18+for+SQL+Server:electores"
        ),
    )
    # Web serve options
    parser.add_argument(
        "--report",
        default="",
        help="[serve] Path to audit JSON report file to review.",
    )
    parser.add_argument(
        "--review-log",
        default="",
        help="[serve] Path to review log JSON. Created automatically if absent.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="[serve] Port for the web review dashboard. Default: 5000.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="[serve] Host for the web review dashboard. Default: 127.0.0.1 (localhost only).",
    )
    parser.add_argument(
        "--matriz",
        default="",
        help="[serve] Path to matriz_certificada.db. Created automatically if absent. "
             "Example: --matriz reports/matriz_certificada.db",
    )
    parser.add_argument(
        "--staging",
        default="",
        help="[pipeline] Path to rd_cee_depurado.db (intermediate staging DB). "
             "Default: reports/rd_cee_depurado.db",
    )
    # Shared options
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory where reports will be written. Default: reports/",
    )
    # Execution control flags (Plan → Aprobar → Ejecutar)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra el plan de ejecucion y sale sin ejecutar nada.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Aprueba el plan automaticamente sin pedir confirmacion interactiva.",
    )
    parser.add_argument(
        "--approved-by",
        default="",
        help="Nombre/ID del auditor que aprueba la ejecucion (registrado en el log).",
    )
    parser.add_argument(
        "--exec-log",
        default="",
        help="Path al SQLite de log de ejecuciones. "
             "Default: <output-dir>/execution_log.db",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"%(prog)s {CLI_VERSION}",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging output.",
    )
    return parser


# ── Path normalization ─────────────────────────────────────────────────────

def normalize_target_paths(raw_paths: list[str] | None, default_path: Path) -> list[Path]:
    """Normalize CLI input paths, skipping any that do not exist with a warning."""
    candidate_paths = [Path(p) for p in raw_paths] if raw_paths else [default_path]
    normalized: list[Path] = []
    for path in candidate_paths:
        resolved = path.expanduser().resolve()
        if not resolved.exists():
            logger.warning("Scan path does not exist, skipping: %s", resolved)
            print(f"  [WARN] Path not found, skipping: {resolved}", file=sys.stderr)
            continue
        normalized.append(resolved)
    return normalized


# ── Terminal output helpers ────────────────────────────────────────────────

def _print_plain_summary(lines: list[str]) -> None:
    sep = "-" * 56
    print()
    print(sep)
    for line in lines:
        print(line)
    print(sep)
    print()


def _print_local_summary(target_paths, total_files, dup_groups, inv_path, dup_path, elapsed):
    total_dupes = sum(len(g.records) for g in dup_groups)
    lines = [
        "  scanner_agent — Local Scan Summary",
        f"  Paths scanned   : {len(target_paths)}",
        *[f"    - {p}" for p in target_paths],
        f"  Files found     : {total_files}",
        f"  Duplicate groups: {len(dup_groups)}",
        *([ f"  Duplicate files : {total_dupes}"] if dup_groups else []),
        f"  Elapsed         : {elapsed:.2f}s",
        "  Reports:",
        f"    - {inv_path}",
        f"    - {dup_path}",
    ]
    if _RICH:
        _print_rich_local(target_paths, total_files, dup_groups, inv_path, dup_path, elapsed)
    else:
        _print_plain_summary(lines)


def _print_rich_local(target_paths, total_files, dup_groups, inv_path, dup_path, elapsed):
    console = _console
    console.print()
    console.rule("[bold blue]scanner_agent — Local Scan Complete[/bold blue]")
    t = Table(box=rich_box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("Key",   style="dim", min_width=20)
    t.add_column("Value", style="bold")
    t.add_row("Paths scanned",    str(len(target_paths)))
    t.add_row("Files found",      str(total_files))
    t.add_row("Duplicate groups", f"[red]{len(dup_groups)}[/red]" if dup_groups else "[green]0[/green]")
    t.add_row("Elapsed",          f"{elapsed:.2f}s")
    t.add_row("Inventory report", str(inv_path))
    t.add_row("Duplicates report",str(dup_path))
    console.print(t)
    if dup_groups:
        dt = Table("Strategy", "Confidence", "Files", box=rich_box.SIMPLE_HEAVY,
                   title="Duplicate Groups", title_style="bold red")
        for g in dup_groups[:10]:
            dt.add_row(g.strategy, f"{g.confidence:.0%}", str(len(g.records)))
        console.print(dt)
    console.print()


def _print_db_summary(source, table, total_rows, match_groups, inv_path, dup_path, elapsed):
    from collections import Counter
    flag_counts = Counter(g.flag for g in match_groups)

    if _RICH:
        _print_rich_db(source, table, total_rows, match_groups, flag_counts,
                       inv_path, dup_path, elapsed)
    else:
        lines = [
            "  scanner_agent — DB Audit Summary",
            f"  Source          : {source} ({table})",
            f"  Rows scanned    : {total_rows}",
            f"  Exact duplicates: {flag_counts.get('EXACT_DUPLICATE', 0)} groups",
            f"  Near-duplicates : {flag_counts.get('NEAR_DUPLICATE', 0)} pairs",
            f"  Address clusters: {flag_counts.get('ADDRESS_CLUSTER', 0)} clusters",
            f"  Possible deceased:{flag_counts.get('POSSIBLE_DECEASED', 0)} records",
            f"  Anomalies       : {flag_counts.get('ANOMALY', 0)} records",
            f"  Elapsed         : {elapsed:.2f}s",
            "  Reports:",
            f"    - {inv_path}",
            f"    - {dup_path}",
        ]
        _print_plain_summary(lines)


def _print_rich_db(source, table, total_rows, match_groups, flag_counts,
                   inv_path, dup_path, elapsed):
    console = _console
    console.print()
    console.rule(f"[bold blue]scanner_agent — DB Audit: {source}[/bold blue]")

    summary = Table(box=rich_box.SIMPLE, show_header=False, padding=(0, 1))
    summary.add_column("Key",   style="dim", min_width=22)
    summary.add_column("Value", style="bold")
    summary.add_row("Source / Table", f"{source} -> {table}")
    summary.add_row("Rows scanned",   str(total_rows))
    summary.add_row("Exact duplicates",
                    f"[red]{flag_counts.get('EXACT_DUPLICATE', 0)}[/red] groups")
    summary.add_row("Near-duplicates",
                    f"[yellow]{flag_counts.get('NEAR_DUPLICATE', 0)}[/yellow] pairs")
    summary.add_row("Address clusters",
                    f"[dark_orange]{flag_counts.get('ADDRESS_CLUSTER', 0)}[/dark_orange] clusters")
    summary.add_row("Possible deceased",
                    f"[cyan]{flag_counts.get('POSSIBLE_DECEASED', 0)}[/cyan] records")
    summary.add_row("Anomalies",
                    f"[magenta]{flag_counts.get('ANOMALY', 0)}[/magenta] records")
    summary.add_row("Elapsed", f"{elapsed:.2f}s")
    summary.add_row("Audit report", str(inv_path))
    summary.add_row("Matches report", str(dup_path))
    console.print(summary)

    sample = match_groups[:8]
    if sample:
        ft = Table("Flag", "Strategy", "Confidence", "Records / Notes",
                   box=rich_box.SIMPLE_HEAVY, title="Sample Findings (first 8)",
                   title_style="bold yellow")
        flag_colors = {
            "EXACT_DUPLICATE":   "red",
            "NEAR_DUPLICATE":    "yellow",
            "ADDRESS_CLUSTER":   "dark_orange",
            "POSSIBLE_DECEASED": "cyan",
            "ANOMALY":           "magenta",
        }
        for g in sample:
            color = flag_colors.get(g.flag, "white")
            names = " / ".join(r.nombre_completo for r in g.records[:2])
            ft.add_row(
                f"[{color}]{g.flag}[/{color}]",
                g.strategy,
                f"{g.confidence:.0%}",
                f"{names[:55]}{'...' if len(names)>55 else ''}",
            )
        console.print(ft)
    console.print()


# ── Pipeline (4-phase depuration) ─────────────────────────────────────────

def _run_pipeline(args, output_dir: Path, scan_started_at, start_time: float) -> None:
    """
    Orquesta el pipeline completo de depuracion en 4 fases:

      Fase 1 — Depuracion interna RD (Registro Demografico)
      Fase 2 — Depuracion interna CEE (padron electoral)
      Fase 3 — Cruce RD x CEE (registros limpios de las fases 1+2)
      Fase 4 — Cruce staging x CESCO (validacion final con Real ID)

    Requiere --source con aliases RD, CEE, y CESCO.
    Produce 4 reportes JSON + rd_cee_depurado.db (staging).
    """
    import json as _json
    import time as _time
    from collections import Counter as _Counter
    from scanner_agent.scanners.db_scanner import scan_db_table, DbScanConfig
    from scanner_agent.detection.record_matcher import run_full_match
    from scanner_agent.detection.cross_db_matcher import run_cross_db_match
    from scanner_agent.detection.clean_exporter import (
        get_flagged_ids, export_clean_records,
        load_staging_records, get_staging_stats,
    )
    from scanner_agent.execution_plan import (
        build_pipeline_plan, display_plan, confirm_execution,
        log_execution_start, log_execution_complete,
    )

    if not args.sources:
        print(
            "[ERROR] --source es requerido para --scanner pipeline.\n"
            "  Se requieren aliases RD, CEE y CESCO:\n"
            "    --source RD:sqlite:///tests/data/mock_rd.db:defunciones\n"
            "    --source CEE:sqlite:///tests/data/mock_cee.db:electores\n"
            "    --source CESCO:sqlite:///tests/data/mock_cesco.db:documentos",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Parsear sources ────────────────────────────────────────────────────
    FIELD_MAPS = {
        "CEE": {
            "row_id": "num_elector", "nombre": "nombre",
            "apellido_paterno": "apellido_paterno", "apellido_materno": "apellido_materno",
            "fecha_nacimiento": "fecha_nacimiento", "edad": "edad", "genero": "genero",
            "direccion": "direccion", "municipio": "municipio",
            "seguro_social": "seguro_social", "telefono": "telefono",
            "tipo_telefono": "tipo_telefono",
        },
        "RD": {
            "row_id": "cert_num", "nombre": "nombre",
            "apellido_paterno": "apellido_paterno", "apellido_materno": "apellido_materno",
            "fecha_nacimiento": "fecha_nacimiento", "seguro_social": "seguro_social",
            "municipio": "municipio_res",
        },
        "CESCO": {
            "row_id": "doc_num", "nombre": "nombre",
            "apellido_paterno": "apellido_paterno", "apellido_materno": "apellido_materno",
            "fecha_nacimiento": "fecha_nacimiento", "genero": "genero",
            "direccion": "direccion", "municipio": "municipio",
            "seguro_social": "seguro_social", "telefono": "telefono",
        },
    }

    source_map: dict[str, tuple[str, str]] = {}   # alias -> (conn_str, table)
    for spec in args.sources:
        parts = spec.split(":", 1)
        if len(parts) < 2:
            print(f"[ERROR] Formato invalido: {spec!r}", file=sys.stderr); sys.exit(1)
        alias = parts[0].upper()
        rest_parts = parts[1].rsplit(":", 1)
        if len(rest_parts) < 2:
            print(f"[ERROR] Falta tabla en: {spec!r}", file=sys.stderr); sys.exit(1)
        source_map[alias] = (rest_parts[0], rest_parts[1])

    for required in ("RD", "CEE", "CESCO"):
        if required not in source_map:
            print(f"[ERROR] Falta --source {required}:...", file=sys.stderr); sys.exit(1)

    staging_path = Path(args.staging).expanduser().resolve() if args.staging \
        else output_dir / "rd_cee_depurado.db"

    exec_log_path = Path(args.exec_log).expanduser().resolve() if args.exec_log \
        else output_dir / "execution_log.db"

    # ── Plan → Aprobar → Ejecutar ──────────────────────────────────────────
    plan = build_pipeline_plan(source_map, staging_path, output_dir)
    display_plan(plan)

    if args.dry_run:
        print("  [--dry-run] Plan mostrado. No se ejecuto ninguna operacion.\n")
        return

    approved = confirm_execution(
        plan,
        require_confirmation=not args.yes,
        approved_by=args.approved_by or "CLI",
    )
    if not approved:
        sys.exit(0)

    exec_id = log_execution_start(plan, exec_log_path,
                                  approved_by=args.approved_by or "CLI")

    # ── Ejecucion ──────────────────────────────────────────────────────────
    sep = "─" * 60
    print(f"\n{sep}")
    print("  scanner_agent — Pipeline de Depuracion (4 fases)")
    print(f"  Staging     : {staging_path}")
    print(f"  Reportes    : {output_dir}/")
    print(f"{sep}\n")

    all_reports: list[Path] = []
    total_findings = 0

    def _load(alias: str) -> list:
        conn_str, table = source_map[alias]
        cfg = DbScanConfig(
            connection_string=conn_str, table=table,
            field_map=FIELD_MAPS.get(alias, {}), source_alias=alias,
        )
        return scan_db_table(cfg)

    def _scan_and_export(alias: str, phase: int, fase_label: str) -> tuple[list, list]:
        """Scan internally, write report, export clean records. Returns (records, findings)."""
        t0 = _time.perf_counter()
        records = _load(alias)
        match_groups = run_full_match(records)
        elapsed = _time.perf_counter() - t0

        findings_dicts = [
            {
                "flag": g.flag, "strategy": g.strategy,
                "confidence": g.confidence, "notes": g.notes,
                "records": [
                    {"row_id": r.row_id, "nombre_completo": r.nombre_completo,
                     "fecha_nacimiento": r.fecha_nacimiento, "municipio": r.municipio,
                     "direccion": r.direccion, "seguro_social": r.seguro_social,
                     "telefono": r.telefono, "source_db": r.source_db}
                    for r in g.records
                ],
            }
            for g in match_groups
        ]

        fc = _Counter(g.flag for g in match_groups)
        scan_meta = {
            "scan_timestamp": scan_started_at.isoformat(),
            "pipeline_phase": phase, "fase_label": fase_label,
            "source": alias, "table": source_map[alias][1],
            "scanner": "pipeline",
            "total_rows_scanned": len(records),
            "exact_duplicate_groups": fc.get("EXACT_DUPLICATE", 0),
            "near_duplicate_pairs": fc.get("NEAR_DUPLICATE", 0),
            "address_clusters": fc.get("ADDRESS_CLUSTER", 0),
            "possible_deceased": fc.get("POSSIBLE_DECEASED", 0),
            "anomalies": fc.get("ANOMALY", 0),
            "duration_seconds": round(elapsed, 3),
        }
        report_path = output_dir / f"pipeline_fase{phase}_{alias.lower()}.json"
        report_path.write_text(
            _json.dumps({"meta": scan_meta, "findings": findings_dicts},
                        indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        flagged = get_flagged_ids(findings_dicts)
        stats = export_clean_records(
            records=records, flagged_ids=flagged,
            dest_db_path=staging_path,
            source_alias=alias, phase=phase, fase_label=fase_label,
        )

        print(f"  Fase {phase} — {fase_label}")
        print(f"    {alias}: {len(records)} registros | {len(match_groups)} hallazgos | "
              f"{stats['exported']} exportados al staging | {stats['skipped_flagged']} con flags")
        print(f"    Reporte: {report_path.name}")
        all_reports.append(report_path)
        return records, findings_dicts

    try:
        # ── FASE 1: Depuracion interna RD ──────────────────────────────────
        print(f"{sep}")
        _, f1 = _scan_and_export("RD", phase=1,
                                 fase_label="Depuracion interna Registro Demografico")
        total_findings += len(f1)

        # ── FASE 2: Depuracion interna CEE ─────────────────────────────────
        print()
        _, f2 = _scan_and_export("CEE", phase=2,
                                 fase_label="Depuracion interna CEE padron electoral")
        total_findings += len(f2)

        # ── FASE 3: Cruce RD x CEE desde staging ──────────────────────────
        print()
        print(f"  Fase 3 — Cruce RD x CEE (registros limpios del staging)")
        t0 = _time.perf_counter()
        rd_clean  = load_staging_records(staging_path, source_filter="RD")
        cee_clean = load_staging_records(staging_path, source_filter="CEE")

        cross_findings = run_cross_db_match({"CEE": cee_clean, "RD": rd_clean})
        elapsed3 = _time.perf_counter() - t0

        fc3 = _Counter(f.flag for f in cross_findings)
        cross_findings_dicts = [
            {
                "flag": f.flag, "strategy": f.strategy,
                "confidence": f.confidence, "notes": f.notes,
                "records": [
                    {"row_id": r.row_id, "nombre_completo": r.nombre_completo,
                     "fecha_nacimiento": r.fecha_nacimiento, "municipio": r.municipio,
                     "seguro_social": r.seguro_social, "source_db": r.source_db}
                    for r in f.records
                ],
            }
            for f in cross_findings
        ]
        meta3 = {
            "scan_timestamp": scan_started_at.isoformat(),
            "pipeline_phase": 3, "fase_label": "Cruce RD x CEE",
            "scanner": "pipeline",
            "rd_clean_records": len(rd_clean),
            "cee_clean_records": len(cee_clean),
            "confirmed_deceased": fc3.get("CONFIRMED_DECEASED", 0),
            "ssn_conflicts": fc3.get("SSN_CROSS_DB_CONFLICT", 0),
            "total_findings": len(cross_findings),
            "duration_seconds": round(elapsed3, 3),
        }
        report3 = output_dir / "pipeline_fase3_rdcee_cross.json"
        report3.write_text(
            _json.dumps({"meta": meta3, "findings": cross_findings_dicts},
                        indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"    RD limpio: {len(rd_clean)} | CEE limpio: {len(cee_clean)} | "
              f"hallazgos cruce: {len(cross_findings)}")
        print(f"    CONFIRMED_DECEASED: {fc3.get('CONFIRMED_DECEASED',0)} | "
              f"SSN_CONFLICT: {fc3.get('SSN_CROSS_DB_CONFLICT',0)}")
        print(f"    Reporte: {report3.name}")
        all_reports.append(report3)
        total_findings += len(cross_findings)

        # ── FASE 4: Cruce staging x CESCO ─────────────────────────────────
        print()
        print(f"  Fase 4 — Validacion final staging x CESCO")
        t0 = _time.perf_counter()
        staging_all = load_staging_records(staging_path)
        cesco_recs  = _load("CESCO")

        final_findings = run_cross_db_match({"STAGING": staging_all, "CESCO": cesco_recs})
        elapsed4 = _time.perf_counter() - t0

        fc4 = _Counter(f.flag for f in final_findings)
        final_findings_dicts = [
            {
                "flag": f.flag, "strategy": f.strategy,
                "confidence": f.confidence, "notes": f.notes,
                "records": [
                    {"row_id": r.row_id, "nombre_completo": r.nombre_completo,
                     "fecha_nacimiento": r.fecha_nacimiento, "municipio": r.municipio,
                     "seguro_social": r.seguro_social, "source_db": r.source_db}
                    for r in f.records
                ],
            }
            for f in final_findings
        ]
        meta4 = {
            "scan_timestamp": scan_started_at.isoformat(),
            "pipeline_phase": 4, "fase_label": "Validacion final CESCO",
            "scanner": "pipeline",
            "staging_records": len(staging_all),
            "cesco_records": len(cesco_recs),
            "name_mismatch": fc4.get("NAME_MISMATCH_CROSS_DB", 0),
            "ssn_conflicts": fc4.get("SSN_CROSS_DB_CONFLICT", 0),
            "total_findings": len(final_findings),
            "duration_seconds": round(elapsed4, 3),
        }
        report4 = output_dir / "pipeline_fase4_cesco_final.json"
        report4.write_text(
            _json.dumps({"meta": meta4, "findings": final_findings_dicts},
                        indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"    Staging: {len(staging_all)} | CESCO: {len(cesco_recs)} | "
              f"hallazgos: {len(final_findings)}")
        print(f"    NAME_MISMATCH: {fc4.get('NAME_MISMATCH_CROSS_DB',0)} | "
              f"SSN_CONFLICT: {fc4.get('SSN_CROSS_DB_CONFLICT',0)}")
        print(f"    Reporte: {report4.name}")
        all_reports.append(report4)
        total_findings += len(final_findings)

    except Exception as exc:
        log_execution_complete(exec_log_path, exec_id, total_findings=total_findings,
                               status="failed", error_message=str(exc))
        raise

    log_execution_complete(exec_log_path, exec_id, total_findings=total_findings)

    # ── Resumen final ──────────────────────────────────────────────────────
    from scanner_agent.detection.clean_exporter import get_staging_stats
    staging_stats = get_staging_stats(staging_path)
    elapsed_total = _time.perf_counter() - start_time
    print(f"\n{sep}")
    print("  RESUMEN DEL PIPELINE")
    print(f"{sep}")
    print(f"  Staging rd_cee_depurado.db  : {staging_stats['total']} registros limpios")
    for src, n in staging_stats.get("por_fuente", {}).items():
        print(f"    {src:<8}: {n}")
    print(f"  Hallazgos totales            : {total_findings}")
    print(f"  Hallazgos Fase 3 (RD x CEE) : {len(cross_findings)}")
    print(f"  Hallazgos Fase 4 (CESCO)     : {len(final_findings)}")
    print(f"  Tiempo total                 : {elapsed_total:.2f}s")
    print(f"  Log de ejecucion             : {exec_log_path}")
    print(f"  Reportes generados ({len(all_reports)}):")
    for rp in all_reports:
        print(f"    {rp}")
    print(f"\n  Siguiente paso:")
    print(f"    scanner-agent --scanner serve --report {all_reports[0]} --matriz <ruta>")
    print(f"    (revisa cada reporte de fase con --report <archivo>)")
    print(f"{sep}\n")


# ── DB scan report writers ─────────────────────────────────────────────────

def _write_db_audit_report(output_path: Path, match_groups, scan_meta: dict) -> None:
    """Write the full audit match groups as a JSON report."""
    import json
    payload = {
        "meta": scan_meta,
        "findings": [
            {
                "flag":       g.flag,
                "strategy":   g.strategy,
                "confidence": g.confidence,
                "notes":      g.notes,
                "records": [
                    {
                        "row_id":            r.row_id,
                        "nombre_completo":   r.nombre_completo,
                        "fecha_nacimiento":  r.fecha_nacimiento,
                        "edad":              r.edad,
                        "municipio":         r.municipio,
                        "direccion":         r.direccion,
                        "seguro_social":     r.seguro_social,
                        "telefono":          r.telefono,
                        "tipo_telefono":     r.tipo_telefono,
                        "source_db":         r.source_db,
                    }
                    for r in g.records
                ],
            }
            for g in match_groups
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Municipio statistics ───────────────────────────────────────────────────

def _municipio_stats(match_groups) -> dict:
    """Return per-municipio counts of findings by flag type."""
    from collections import defaultdict
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {
        "EXACT_DUPLICATE": 0, "NEAR_DUPLICATE": 0,
        "POSSIBLE_DECEASED": 0, "ANOMALY": 0, "ADDRESS_CLUSTER": 0, "total": 0,
    })
    for g in match_groups:
        muns = {r.municipio for r in g.records if r.municipio}
        for mun in muns:
            stats[mun][g.flag] = stats[mun].get(g.flag, 0) + 1
            stats[mun]["total"] += 1
    return {k: dict(v) for k, v in sorted(stats.items(), key=lambda x: -x[1]["total"])}


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    settings = Settings.load()
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        configure_logging("DEBUG")
        logger.debug("Verbose mode enabled.")
    else:
        configure_logging(settings.log_level)

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    scan_started_at = datetime.datetime.now(datetime.timezone.utc)
    start_time = time.perf_counter()

    # ── LOCAL scanner ──────────────────────────────────────────────────────
    if args.scanner == "local":
        default_path = Path(settings.default_scan_path).expanduser().resolve()
        target_paths = normalize_target_paths(args.path, default_path)
        if not target_paths:
            print("[ERROR] No valid paths to scan. Exiting.", file=sys.stderr)
            sys.exit(1)

        all_files = scan_local_paths(target_paths)
        total_files = len(all_files)
        dup_groups = group_exact_duplicates(all_files)
        elapsed = time.perf_counter() - start_time

        inv_path = output_dir / "file_inventory.json"
        dup_path = output_dir / "duplicate_report.json"
        write_inventory_report(all_files, inv_path)
        write_duplicates_report(dup_groups, dup_path)
        _print_local_summary(target_paths, total_files, dup_groups, inv_path, dup_path, elapsed)

    # ── DB scanner ─────────────────────────────────────────────────────────
    elif args.scanner == "db":
        if not args.db:
            print("[ERROR] --db is required for --scanner db.", file=sys.stderr)
            sys.exit(1)

        ELECTORAL_FIELD_MAP = {
            "row_id":           "num_elector",
            "nombre":           "nombre",
            "apellido_paterno": "apellido_paterno",
            "apellido_materno": "apellido_materno",
            "fecha_nacimiento": "fecha_nacimiento",
            "edad":             "edad",
            "genero":           "genero",
            "direccion":        "direccion",
            "municipio":        "municipio",
            "seguro_social":    "seguro_social",
            "telefono":         "telefono",
            "tipo_telefono":    "tipo_telefono",
        }

        cfg = DbScanConfig(
            connection_string=args.db,
            table=args.table,
            field_map=ELECTORAL_FIELD_MAP,
            source_alias=args.source_alias or args.table,
        )
        records = scan_db_table(cfg)
        match_groups = run_full_match(records)
        elapsed = time.perf_counter() - start_time

        source = cfg.source_label
        scan_meta = {
            "scan_timestamp":        scan_started_at.isoformat(),
            "source":                source,
            "table":                 args.table,
            "scanner":               "db",
            "total_rows_scanned":    len(records),
            "total_match_groups":    len(match_groups),
            "duration_seconds":      round(elapsed, 3),
        }

        report_path = output_dir / f"audit_{source.lower()}.json"
        inv_path    = output_dir / f"inventory_{source.lower()}.json"
        _write_db_audit_report(report_path, match_groups, scan_meta)
        write_inventory_report(records, inv_path)
        _print_db_summary(source, args.table, len(records), match_groups,
                          inv_path, report_path, elapsed)

    # ── CROSS-DB scanner ────────────────────────────────────────────────────
    elif args.scanner == "cross-db":
        import json as _json
        from scanner_agent.detection.cross_db_matcher import run_cross_db_match

        if not args.sources:
            print(
                "[ERROR] --source es requerido para --scanner cross-db.\n"
                "  Ejemplo: --source CEE:sqlite:///tests/data/mock_cee.db:electores",
                file=sys.stderr,
            )
            sys.exit(1)

        CROSS_FIELD_MAPS = {
            "CEE": {
                "row_id": "num_elector", "nombre": "nombre",
                "apellido_paterno": "apellido_paterno", "apellido_materno": "apellido_materno",
                "fecha_nacimiento": "fecha_nacimiento", "edad": "edad", "genero": "genero",
                "direccion": "direccion", "municipio": "municipio",
                "seguro_social": "seguro_social", "telefono": "telefono",
                "tipo_telefono": "tipo_telefono",
            },
            "RD": {
                "row_id": "cert_num", "nombre": "nombre",
                "apellido_paterno": "apellido_paterno", "apellido_materno": "apellido_materno",
                "fecha_nacimiento": "fecha_nacimiento", "seguro_social": "seguro_social",
                "municipio": "municipio_res",
            },
            "CESCO": {
                "row_id": "doc_num", "nombre": "nombre",
                "apellido_paterno": "apellido_paterno", "apellido_materno": "apellido_materno",
                "fecha_nacimiento": "fecha_nacimiento", "genero": "genero",
                "direccion": "direccion", "municipio": "municipio",
                "seguro_social": "seguro_social", "telefono": "telefono",
            },
        }

        source_records: dict[str, list] = {}
        for spec in args.sources:
            parts = spec.split(":", 1)
            if len(parts) < 2:
                print(f"[ERROR] Formato invalido: {spec!r}", file=sys.stderr); sys.exit(1)
            alias = parts[0].upper()
            rest_parts = parts[1].rsplit(":", 1)
            if len(rest_parts) < 2:
                print(f"[ERROR] Falta tabla en: {spec!r}", file=sys.stderr); sys.exit(1)
            conn_str, table = rest_parts[0], rest_parts[1]
            fmap = CROSS_FIELD_MAPS.get(alias, {
                "row_id": "id", "nombre": "nombre",
                "apellido_paterno": "apellido_paterno", "apellido_materno": "apellido_materno",
                "fecha_nacimiento": "fecha_nacimiento", "seguro_social": "seguro_social",
            })
            cfg = DbScanConfig(
                connection_string=conn_str, table=table,
                field_map=fmap, source_alias=alias,
            )
            print(f"  Cargando {alias} ({table})...", end=" ", flush=True)
            records = scan_db_table(cfg)
            source_records[alias] = records
            print(f"{len(records)} registros")

        t0 = time.perf_counter()
        findings = run_cross_db_match(source_records)
        elapsed = time.perf_counter() - start_time

        from collections import Counter
        fc = Counter(f.flag for f in findings)
        print(f"\n  Hallazgos cross-DB: {len(findings)}")
        for flag, n in fc.most_common():
            print(f"    {flag}: {n}")

        findings_dicts = [
            {
                "flag": f.flag, "strategy": f.strategy,
                "confidence": f.confidence, "notes": f.notes,
                "records": [
                    {"row_id": r.row_id, "nombre_completo": r.nombre_completo,
                     "fecha_nacimiento": r.fecha_nacimiento, "municipio": r.municipio,
                     "seguro_social": r.seguro_social, "source_db": r.source_db}
                    for r in f.records
                ],
            }
            for f in findings
        ]
        sources_label = "_".join(sorted(source_records.keys())).lower()
        report_path = output_dir / f"audit_cross_db_{sources_label}.json"
        scan_meta = {
            "scan_timestamp": scan_started_at.isoformat(),
            "scanner": "cross-db",
            "sources": {alias: len(recs) for alias, recs in source_records.items()},
            "total_findings": len(findings),
            "duration_seconds": round(elapsed, 3),
        }
        report_path.write_text(
            _json.dumps({"meta": scan_meta, "findings": findings_dicts},
                        indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n  Reporte: {report_path}")
        print(f"  Tiempo : {elapsed:.2f}s")

    # ── PIPELINE scanner ────────────────────────────────────────────────────
    elif args.scanner == "pipeline":
        _run_pipeline(args, output_dir, scan_started_at, start_time)

    # ── NOMINA scanner ──────────────────────────────────────────────────────
    elif args.scanner == "nomina":
        import json as _json
        from scanner_agent.detection.nomina_scanner import run_nomina_scan
        from scanner_agent.execution_plan import (
            build_nomina_plan, display_plan, confirm_execution,
            log_execution_start, log_execution_complete,
        )
        from scanner_agent.models import AuditDomain

        if not args.sources:
            print(
                "[ERROR] --source es requerido para --scanner nomina.\n"
                "  Formato: ALIAS:connection_string:table\n"
                "  Requerido: NOMINA\n"
                "  Opcional:  RD (para detectar DECEASED_EMPLOYEE)\n"
                "  Ejemplo:\n"
                "    --source NOMINA:sqlite:///tests/data/mock_nomina.db:nomina_empleados\n"
                "    --source RD:sqlite:///tests/data/mock_rd.db:defunciones",
                file=sys.stderr,
            )
            sys.exit(1)

        # Parsear sources
        nomina_conn = nomina_table = None
        rd_conn = rd_table = None

        for spec in args.sources:
            parts = spec.split(":", 1)
            if len(parts) < 2:
                print(f"[ERROR] Formato invalido: {spec!r}", file=sys.stderr); sys.exit(1)
            alias = parts[0].upper()
            rest_parts = parts[1].rsplit(":", 1)
            if len(rest_parts) < 2:
                print(f"[ERROR] Falta tabla en: {spec!r}", file=sys.stderr); sys.exit(1)
            conn_str, table = rest_parts[0], rest_parts[1]
            if alias == "NOMINA":
                nomina_conn, nomina_table = conn_str, table
            elif alias == "RD":
                rd_conn, rd_table = conn_str, table

        if not nomina_conn:
            print("[ERROR] Falta --source NOMINA:...", file=sys.stderr)
            sys.exit(1)

        exec_log_path = Path(args.exec_log).expanduser().resolve() if args.exec_log \
            else output_dir / "execution_log.db"

        # ── Plan → Aprobar → Ejecutar ──────────────────────────────────────
        plan = build_nomina_plan(
            nomina_conn=nomina_conn, nomina_table=nomina_table,
            rd_conn=rd_conn, rd_table=rd_table,
            output_dir=output_dir,
        )
        display_plan(plan)

        if args.dry_run:
            print("  [--dry-run] Plan mostrado. No se ejecuto ninguna operacion.\n")
            return

        approved = confirm_execution(
            plan,
            require_confirmation=not args.yes,
            approved_by=args.approved_by or "CLI",
        )
        if not approved:
            sys.exit(0)

        exec_id = log_execution_start(plan, exec_log_path,
                                      approved_by=args.approved_by or "CLI")

        try:
            # Cargar registros de nomina
            print(f"\n  Cargando NOMINA ({nomina_table})...", end=" ", flush=True)
            nomina_cfg = DbScanConfig(
                connection_string=nomina_conn,
                table=nomina_table,
                field_map=NOMINA_FIELD_MAP,
                source_alias="NOMINA",
            )
            nomina_records = scan_db_table(nomina_cfg)
            for rec in nomina_records:
                object.__setattr__(rec, "source_domain", AuditDomain.NOMINA)
            print(f"{len(nomina_records)} registros")

            # Cargar RD si disponible
            rd_records = []
            if rd_conn:
                RD_FIELD_MAP = {
                    "row_id": "cert_num", "nombre": "nombre",
                    "apellido_paterno": "apellido_paterno", "apellido_materno": "apellido_materno",
                    "fecha_nacimiento": "fecha_nacimiento", "seguro_social": "seguro_social",
                    "municipio": "municipio_res",
                }
                print(f"  Cargando RD ({rd_table})...", end=" ", flush=True)
                rd_cfg = DbScanConfig(
                    connection_string=rd_conn, table=rd_table,
                    field_map=RD_FIELD_MAP, source_alias="RD",
                )
                rd_records = scan_db_table(rd_cfg)
                print(f"{len(rd_records)} registros")

            # Ejecutar escaneo de nomina
            results = run_nomina_scan(nomina_records, rd_records=rd_records if rd_records else None)
            elapsed = time.perf_counter() - start_time

            total_findings = len(results["findings"])
            log_execution_complete(exec_log_path, exec_id, total_findings=total_findings)

        except Exception as exc:
            log_execution_complete(exec_log_path, exec_id, total_findings=0,
                                   status="failed", error_message=str(exc))
            raise

        # Serializar y guardar reporte
        scan_meta = {
            "scan_timestamp":    scan_started_at.isoformat(),
            "scanner":           "nomina",
            "nomina_source":     f"{nomina_conn}:{nomina_table}",
            "rd_source":         f"{rd_conn}:{rd_table}" if rd_conn else None,
            "total_records":     results["total_records"],
            "total_findings":    total_findings,
            "summary":           results["summary"],
            "duration_seconds":  round(elapsed, 3),
            "exec_log":          str(exec_log_path),
        }
        report_path = output_dir / "audit_nomina.json"
        report_path.write_text(
            _json.dumps({"meta": scan_meta, "findings": results["findings"]},
                        indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Resumen terminal
        sep = "─" * 60
        print(f"\n{sep}")
        print("  scanner_agent — Auditoria de Nomina")
        print(f"{sep}")
        print(f"  Registros escaneados : {results['total_records']}")
        summary = results["summary"]
        print(f"  MULTI_AGENCY         : {summary.get('MULTI_AGENCY_EMPLOYEE', 0)} grupos")
        print(f"  DECEASED_EMPLOYEE    : {summary.get('DECEASED_EMPLOYEE', 0)} empleados")
        print(f"  SALARY_ANOMALY       : {summary.get('SALARY_ANOMALY', 0)} anomalias")
        print(f"  Total hallazgos      : {total_findings}")
        print(f"  Tiempo               : {elapsed:.2f}s")
        print(f"  Log de ejecucion     : {exec_log_path}")
        print(f"  Reporte              : {report_path}")
        print(f"{sep}\n")
        print(f"  Revisar en dashboard:")
        print(f"    scanner-agent --scanner serve --report {report_path} --matriz <ruta>")
        print()

    # ── SERVE (web dashboard) ───────────────────────────────────────────────
    elif args.scanner == "serve":
        from scanner_agent.web.app import create_app

        report_path = args.report or ""
        review_log  = args.review_log or str(output_dir / "review_log.json")
        matriz_path = args.matriz or str(output_dir / "matriz_certificada.db")

        app = create_app(
            report_path=report_path,
            review_log_path=review_log,
            matriz_db_path=matriz_path,
        )
        print(f"\n  scanner_agent — Dashboard de Revision")
 