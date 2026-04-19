import argparse
import logging
from pathlib import Path

from scanner_agent.config import Settings
from scanner_agent.detection.duplicate_engine import group_exact_duplicates
from scanner_agent.detection.hash_engine import sha256_file
from scanner_agent.logging_config import configure_logging
from scanner_agent.reporting.report_writer import (
    write_duplicates_report,
    write_inventory_report,
)
from scanner_agent.scanners.local_scanner import scan_local_paths

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scanner-agent",
        description="Safe-first file intelligence and duplicate detection engine.",
    )
    parser.add_argument(
        "--path",
        action="append",
        help="Path to scan. You can provide this option multiple times.",
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory where reports will be written.",
    )
    return parser


def main() -> None:
    settings = Settings.load()
    configure_logging(settings.log_level)

    parser = build_parser()
    args = parser.parse_args()

    target_paths = [Path(p) for p in args.path] if args.path else [settings.default_scan_path]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Starting scanner_agent in safe mode.")
    logger.info("Delete operations are disabled in Phase 1.")
    logger.info("Scanning %d path(s).", len(target_paths))

    records = scan_local_paths(target_paths)

    for record in records:
        try:
            record.sha256 = sha256_file(record.path)
        except OSError as exc:
            logger.warning("Could not hash %s: %s", record.path, exc)

    duplicate_groups = group_exact_duplicates(records)

    write_inventory_report(output_dir / "inventory.json", records)
    write_duplicates_report(output_dir / "duplicates.json", duplicate_groups)

    logger.info("Inventory report written to %s", output_dir / "inventory.json")
    logger.info("Duplicate report written to %s", output_dir / "duplicates.json")
    logger.info("Detected %d exact duplicate group(s).", len(duplicate_groups))