from collections import defaultdict

from scanner_agent.models import DuplicateGroup, FileRecord


def group_exact_duplicates(records: list[FileRecord]) -> list[DuplicateGroup]:
    grouped: dict[tuple[str, int], list[FileRecord]] = defaultdict(list)

    for record in records:
        if record.sha256 is None:
            continue
        grouped[(record.sha256, record.size_bytes)].append(record)

    results: list[DuplicateGroup] = []
    for _, items in grouped.items():
        if len(items) > 1:
            results.append(
                DuplicateGroup(
                    strategy="exact_hash_match",
                    confidence=1.0,
                    records=items,
                )
            )
    return results