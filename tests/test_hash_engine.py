from pathlib import Path

from scanner_agent.detection.hash_engine import sha256_file


def test_sha256_file_returns_hash(tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("hello world", encoding="utf-8")

    result = sha256_file(sample)

    assert isinstance(result, str)
    assert len(result) == 64