from pathlib import Path

SYSTEM_DIR_NAMES = {
    "Windows",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "$Recycle.Bin",
    "AppData",
    "System Volume Information",
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
}

PROJECT_METADATA_NAMES = {
    ".gitignore",
    ".gitattributes",
    ".idea",
    ".vscode",
    ".iml",
}

SAFE_EXTENSION_DENYLIST = {
    ".dll",
    ".sys",
    ".drv",
    ".ini",
}


def is_excluded_path(path: Path, scan_root: "Path | None" = None) -> bool:
    """Return True if *path* should be excluded based on directory-name rules.

    When *scan_root* is supplied, only the path components **relative** to the
    scan root are checked.  This avoids false exclusions when the scan root
    itself sits inside a system directory (e.g. pytest tmp_path on Windows
    lives under C:\\Users\\...\\AppData\\Local\\Temp\\...).

    Falls back to checking absolute path parts when *scan_root* is not given
    or when the path cannot be made relative to the root.
    """
    if scan_root is not None:
        try:
            rel_parts = path.relative_to(scan_root).parts
            return any(part in SYSTEM_DIR_NAMES for part in rel_parts)
        except ValueError:
            pass  # path is not under scan_root -- fall through to absolute check
    return any(part in SYSTEM_DIR_NAMES for part in path.parts)


def is_excluded_file(path: Path) -> bool:
    if path.name in PROJECT_METADATA_NAMES:
        return True
    if path.suffix.lower() in SAFE_EXTENSION_DENYLIST:
        return True
    return False
