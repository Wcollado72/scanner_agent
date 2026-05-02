import json
from pathlib import Path
from typing import Any

from scanner_agent.models import DuplicateGroup, FileRecord


def write_inventory_report(
    output_path: Path,
    records: list[FileRecord],
    scan_meta: dict[str, Any] | None = None,
) -> None:
    """Write the full file inventory to a JSON report."""
    payload: dict[str, Any] = {}

    if scan_meta:
        payload["meta"] = scan_meta

    payload["files"] = [
        {
            "path": str(record.path),
            "source": record.source,
            "size_bytes": record.size_bytes,
            "modified_time": record.modified_time,
            "sha256": record.sha256,
            "extension": record.extension,
        }
        for record in records
    ]

    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_duplicates_report(
    output_path: Path,
    groups: list[DuplicateGroup],
    scan_meta: dict[str, Any] | None = None,
) -> None:
    """Write duplicate groups to a JSON report."""
    payload: dict[str, Any] = {}

    if scan_meta:
        payload["meta"] = scan_meta

    payload["duplicate_groups"] = [
        {
            "strategy": group.strategy,
            "confidence": group.confidence,
            "records": [
                {
                    "path": str(record.path),
                    "source": record.source,
                    "size_bytes": record.size_bytes,
                    "sha256": record.sha256,
                }
                for record in group.records
            ],
        }
        for group in groups
    ]

    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
