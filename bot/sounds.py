from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = frozenset({".ogg", ".opus", ".mp3", ".wav"})


def list_sounds(directory: Path) -> list[Path]:
    """Звуковые файлы поддерживаемых форматов в папке (без подпапок)."""
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
