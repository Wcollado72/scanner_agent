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

CLI_VERSION = "0.4.0"

SUPPORTED_SCANNERS = ("local", "db", "serve")

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
    # Shared options
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory where reports will be written. Default: reports/",
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
            "EXACT_DUPLICATE": "red",
            "NEAR_DUPLICATE":  "yellow",
            "POSSIBLE_DECEASED": "cyan",
            "ANOMALY": "magenta",
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
        "POSSIBLE_DECEASED": 0, "ANOMALY": 0, "total": 0,
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
        target_paths = normalize_target_paths(args.path, settings.default_scan_path)
        if not target_paths:
            print("[ERROR] No valid scan paths provided. Aborting.", file=sys.stderr)
            sys.exit(1)

        logger.info("Starting local scan of %d path(s).", len(target_paths))
        records = scan_local_paths(target_paths)

        for record in records:
            try:
                record.sha256 = sha256_file(record.path)
            except OSError as exc:
                logger.warning("Could not hash %s: %s", record.path, exc)

        dup_groups = group_exact_duplicates(records)
        elapsed = time.perf_counter() - start_time

        scan_meta = {
            "scan_timestamp":        scan_started_at.isoformat(),
            "scan_targets":          [str(p) for p in target_paths],
            "scanner":               "local",
            "total_files_scanned":   len(records),
            "duplicate_groups_found": len(dup_groups),
            "duration_seconds":      round(elapsed, 3),
        }

        inv_path = output_dir / "inventory.json"
        dup_path = output_dir / "duplicates.json"
        write_inventory_report(inv_path, records, scan_meta)
        write_duplicates_report(dup_path, dup_groups, scan_meta)

        logger.info("Scanned %d files in %.2fs — %d duplicate groups.",
                    len(records), elapsed, len(dup_groups))

        _print_local_summary(target_paths, len(records), dup_groups, inv_path, dup_path, elapsed)

    # ── DB scanner ────────────────────────────────────────────────────────
    elif args.scanner == "db":
        if not args.db:
            print(
                "[ERROR] --db is required for --scanner db.\n"
                "  Example: --db sqlite:///path/to/cee.db --table electores",
                file=sys.stderr,
            )
            sys.exit(1)

        # Default CEE field map — user can extend via config in a future version
        CEE_FIELD_MAP = {
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

        db_config = DbScanConfig(
            connection_string=args.db,
            table=args.table,
            field_map=CEE_FIELD_MAP,
            source_alias=args.source_alias or args.table.upper(),
        )

        logger.info("Starting DB scan: %s -> %s", db_config.source_label, args.table)

        try:
            row_records = scan_db_table(db_config)
        except Exception as exc:
            print(f"[ERROR] DB scan failed: {exc}", file=sys.stderr)
            logger.error("DB scan failed: %s", exc)
            sys.exit(1)

        match_groups = run_full_match(row_records)
        elapsed = time.perf_counter() - start_time

        from collections import Counter
        flag_counts = Counter(g.flag for g in match_groups)
        ssn_conflicts = sum(1 for g in match_groups if g.strategy == "ssn_identity_conflict")

        scan_meta = {
            "scan_timestamp":         scan_started_at.isoformat(),
            "source":                 db_config.source_label,
            "table":                  args.table,
            "scanner":                "db",
            "total_rows_scanned":     len(row_records),
            "exact_duplicate_groups": flag_counts.get("EXACT_DUPLICATE", 0),
            "near_duplicate_pairs":   flag_counts.get("NEAR_DUPLICATE", 0),
            "possible_deceased":      flag_counts.get("POSSIBLE_DECEASED", 0),
            "anomalies":              flag_counts.get("ANOMALY", 0),
            "ssn_identity_conflicts":  ssn_conflicts,
            "duration_seconds":       round(elapsed, 3),
            "municipio_stats":        _municipio_stats(match_groups),
        }

        audit_path = output_dir / f"audit_{db_config.source_label.lower()}.json"
        _write_db_audit_report(audit_path, match_groups, scan_meta)
        logger.info("Audit report written: %s (%d findings)", audit_path, len(match_groups))

        _print_db_summary(
            source=db_config.source_label,
            table=args.table,
            total_rows=len(row_records),
            match_groups=match_groups,
            inv_path=audit_path,
            dup_path=audit_path,
            elapsed=elapsed,
        )

    # ── WEB SERVE ─────────────────────────────────────────────────────────
    elif args.scanner == "serve":
        if not args.report:
            print(
                "[ERROR] --report is required for --scanner serve.\n"
                "  Example: --scanner serve --report reports/audit_cee.json",
                file=sys.stderr,
            )
            sys.exit(1)

        report_path = Path(args.report).expanduser().resolve()
        if not report_path.exists():
            print(f"[ERROR] Report file not found: {report_path}", file=sys.stderr)
            sys.exit(1)

        review_log_path = (
            Path(args.review_log).expanduser().resolve()
            if args.review_log
            else report_path.parent / "review_log.json"
        )

        from scanner_agent.web.app import app as flask_app, init_app
        init_app(report_path, review_log_path)

        sep = "-" * 56
        print()
        print(sep)
        print("  scanner_agent — Web Review Dashboard")
        print(f"  Report     : {report_path}")
        print(f"  Review log : {review_log_path}")
        print(f"  URL        : http://{args.host}:{args.port}")
        print(f"  Roles      : set REVIEWER_PASSWORD / ADMIN_PASSWORD in .env")
        print(f"  Press Ctrl+C to stop.")
        print(sep)
        print()

        flask_app.run(host=args.host, port=args.port, debug=False)
