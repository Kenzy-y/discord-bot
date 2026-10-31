from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = frozenset({".ogg", ".opus", ".mp3", ".wav"})
SOUND_TYPES = ("short", "long")


def list_sounds(directory: Path) -> list[Path]:
    """Звуковые файлы поддерживаемых форматов в папке (без подпапок)."""
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def get_sounds_by_type(directory: Path) -> dict[str, list[Path]]:
    """Возвращает словарь {тип: список_файлов} для подпапок short и long.

    Если существуют подпапки short/ или long/ с файлами, используются они.
    Если ни в одной из подпапок файлов нет, проверяется сама папка directory
    (обратная совместимость с плоской структурой).
    """
    if not directory.is_dir():
        return {}
    by_type: dict[str, list[Path]] = {}
    for st in SOUND_TYPES:
        sub = directory / st
        if sub.is_dir():
            files = list_sounds(sub)
            if files:
                by_type[st] = files
    if not by_type:
        flat = list_sounds(directory)
        if flat:
            by_type["short"] = flat
    return by_type


def list_all_sounds(directory: Path) -> list[Path]:
    """Возвращает все звуковые файлы (из подпапок по типам или напрямую из директории)."""
    by_type = get_sounds_by_type(directory)
    all_files: list[Path] = []
    for files in by_type.values():
        all_files.extend(files)
    return sorted(all_files, key=lambda p: p.name)

