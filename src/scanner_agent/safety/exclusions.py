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


def is_excluded_path(path: Path) -> bool:
    return any(part in SYSTEM_DIR_NAMES for part in path.parts)


def is_excluded_file(path: Path) -> bool:
    if path.name in PROJECT_METADATA_NAMES:
        return True
    if path.suffix.lower() in SAFE_EXTENSION_DENYLIST:
        return True
    return False