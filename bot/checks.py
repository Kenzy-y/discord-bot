"""Проверки окружения при запуске и перед воспроизведением."""

from __future__ import annotations

import importlib.util
import logging
import shutil

from .paths import SOUNDS_DIR
from .sounds import list_all_sounds

log = logging.getLogger(__name__)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def probe_method() -> str:
    """Метод для FFmpegOpusAudio.from_probe: ffprobe, если есть, иначе сам ffmpeg."""
    return "native" if shutil.which("ffprobe") else "fallback"


def run_startup_checks() -> None:
    if not ffmpeg_available():
        log.warning("FFmpeg не найден в PATH — воспроизведение работать не будет. Установите FFmpeg (см. README).")
    elif probe_method() == "fallback":
        log.info("ffprobe не найден, для определения кодека будет использоваться ffmpeg.")

    if importlib.util.find_spec("davey") is None:
        log.warning(
            "Пакет davey не установлен — подключение к голосу не сработает (DAVE, код 4017). "
            "Установите зависимости: pip install -r requirements.txt"
        )

    if not SOUNDS_DIR.is_dir():
        log.warning("Папка %s не найдена — звуков нет.", SOUNDS_DIR)
    elif not list_all_sounds(SOUNDS_DIR):
        log.warning("В папке %s нет звуков (.ogg, .opus, .mp3, .wav).", SOUNDS_DIR)
