from pathlib import Path
import logging

from scanner_agent.models import FileRecord
from scanner_agent.safety.exclusions import is_excluded_file, is_excluded_path

logger = logging.getLogger(__name__)


def scan_local_paths(paths: list[Path]) -> list[FileRecord]:
    records: list[FileRecord] = []

    for root_path in paths:
        if not root_path.exists():
            logger.warning("Path does not exist: %s", root_path)
            continue

        for path in root_path.rglob("*"):
            if path.is_dir():
                continue
            if is_excluded_path(path):
                continue
            if is_excluded_file(path):
                continue

            try:
                stats = path.stat()
                records.append(
                    FileRecord(
                        path=path,
                        source="local",
                        size_bytes=stats.st_size,
                        modified_time=stats.st_mtime,
                        extension=path.suffix.lower(),
                    )
                )
            except OSError as exc:
                logger.warning("Could not inspect %s: %s", path, exc)

    return records