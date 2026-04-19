from pathlib import Path

from scanner_agent.safety.exclusions import is_excluded_file, is_excluded_path


def test_excluded_system_path() -> None:
    assert is_excluded_path(Path("C:/Users/test/AppData/file.txt")) is True


def test_excluded_project_file() -> None:
    assert is_excluded_file(Path(".gitignore")) is True


def test_non_excluded_regular_file() -> None:
    assert is_excluded_file(Path("notes/report.pdf")) is False