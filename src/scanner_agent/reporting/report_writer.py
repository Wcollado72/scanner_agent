from pathlib import Path
import json

from scanner_agent.models import DuplicateGroup, FileRecord


def write_inventory_report(output_path: Path, records: list[FileRecord]) -> None:
    payload = [
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


def write_duplicates_report(output_path: Path, groups: list[DuplicateGroup]) -> None:
    payload = [
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