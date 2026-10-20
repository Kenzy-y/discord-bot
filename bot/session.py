"""Сессии случайного воспроизведения: одна бесконечная фоновая задача на сервер."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from pathlib import Path

import discord

from .checks import probe_method
from .config import ConfigReloader
from .paths import SOUNDS_DIR
from .selector import Choice, SoundSelector
from .sounds import list_sounds

log = logging.getLogger(__name__)

# После стольких ошибок воспроизведения подряд сессия завершается
MAX_FAILURES_IN_ROW = 5

Player = Callable[[discord.VoiceClient, Path], Awaitable[None]]
Sleep = Callable[[float], Awaitable[object]]
SelectorFactory = Callable[[], SoundSelector]


class PlaybackError(Exception):
    """Файл не удалось воспроизвести."""


class _CountingSource(discord.AudioSource):
    """Обёртка, считающая отданные пакеты: FFmpeg на битом файле молча отдаёт 0 пакетов."""

    def __init__(self, inner: discord.AudioSource) -> None:
        self.inner = inner
        self.packets = 0

    @property
    def _current_error(self) -> Exception | None:
        # AudioPlayer берёт отсюда ошибку процесса FFmpeg
        return getattr(self.inner, "_current_error", None)

    def read(self) -> bytes:
        data = self.inner.read()
        if data:
            self.packets += 1
        return data

    def is_opus(self) -> bool:
        return self.inner.is_opus()

    def cleanup(self) -> None:
        self.inner.cleanup()


async def play_file(vc: discord.VoiceClient, path: Path) -> None:
    """Проигрывает файл до конца. Opus-файлы идут без перекодирования."""
    if not path.is_file():
        raise PlaybackError("файл не найден")

    loop = asyncio.get_running_loop()
    finished: asyncio.Future[Exception | None] = loop.create_future()

    def resolve(error: Exception | None) -> None:
        if not finished.done():
            finished.set_result(error)

    def after(error: Exception | None) -> None:
        # Вызывается из потока плеера
        loop.call_soon_threadsafe(resolve, error)

    source = _CountingSource(
        await discord.FFmpegOpusAudio.from_probe(str(path), method=probe_method())
    )
    try:
        vc.play(source, after=after)
    except Exception:
        source.cleanup()
        raise
    error = await finished
    if error is not None:
        raise PlaybackError(str(error)) from error
    if source.packets == 0 and vc.is_connected():
        raise PlaybackError("FFmpeg не смог прочитать звук из файла")


class SessionManager:
    def __init__(
        self,
        *,
        sounds_dir: Path = SOUNDS_DIR,
        player: Player = play_file,
        sleep: Sleep = asyncio.sleep,
        selector_factory: SelectorFactory = SoundSelector,
    ) -> None:
        self._tasks: dict[int, asyncio.Task[None]] = {}
        # Селекторы переживают завершение сессии, чтобы после /stop и /start
        # не играл сразу тот же звук. Живут, пока запущен бот.
        self._selectors: dict[int, SoundSelector] = {}
        self._sounds_dir = sounds_dir
        self._player = player
        self._sleep = sleep
        self._selector_factory = selector_factory

    def selector(self, guild_id: int) -> SoundSelector:
        selector = self._selectors.get(guild_id)
        if selector is None:
            selector = self._selectors[guild_id] = self._selector_factory()
        return selector

    def is_running(self, guild_id: int) -> bool:
        task = self._tasks.get(guild_id)
        return task is not None and not task.done()

    def start(self, guild: discord.Guild, vc: discord.VoiceClient, config: ConfigReloader) -> asyncio.Task[None]:
        task = asyncio.create_task(self._run(guild, vc, config), name=f"session-{guild.id}")
        self._tasks[guild.id] = task
        return task

    def cancel(self, guild_id: int) -> None:
        task = self._tasks.get(guild_id)
        if task is not None and not task.done():
            task.cancel()

    async def stop(self, guild_id: int) -> bool:
        """Останавливает сессию и ждёт её завершения. False, если сессии не было."""
        task = self._tasks.get(guild_id)
        if task is None or task.done():
            return False
        task.cancel()
        await asyncio.wait({task})
        return True

    async def stop_all(self) -> None:
        for guild_id in list(self._tasks):
            await self.stop(guild_id)

    async def _run(self, guild: discord.Guild, vc: discord.VoiceClient, reloader: ConfigReloader) -> None:
        log.info("Сервер %s: сессия началась", guild.name)
        reason = "?"
        number = 0
        failures = 0
        selector = self.selector(guild.id)
        logged_window: int | None = None
        logged_unknown: tuple[str, ...] = ()
        try:
            while True:
                # Конфиг и список звуков перечитываются перед каждым звуком, чтобы
                # изменения после git pull применялись без перезапуска сессии
                config = reloader.get()
                playback = config.playback
                files = list_sounds(self._sounds_dir)
                if not files:
                    reason = "в папке sounds/ не осталось звуков"
                    break

                choice = selector.choose(files, config.selection, config.weights)
                if choice is None:
                    reason = "у всех звуков в папке вес 0"
                    break
                _log_choice_table(guild, choice)
                if choice.unknown_weights != logged_unknown:
                    logged_unknown = choice.unknown_weights
                    if logged_unknown:
                        log.warning(
                            "Сервер %s: в [weights] есть звуки, которых нет в sounds/: %s",
                            guild.name, ", ".join(logged_unknown),
                        )
                if choice.window != logged_window:
                    window = config.selection.window
                    if choice.window < window:
                        log.info(
                            "Сервер %s: звуков мало, окно повторов сужено с %d до %d",
                            guild.name, window, choice.window,
                        )
                    elif logged_window is not None:
                        log.info("Сервер %s: окно повторов снова %d", guild.name, choice.window)
                    logged_window = choice.window

                number += 1
                path = choice.path
                log.info(
                    "Сервер %s: [%d] играю %s (шанс %.0f%%, звуков в папке: %d)",
                    guild.name, number, path.name, choice.chance * 100, len(files),
                )
                try:
                    await self._player(vc, path)
                    failures = 0
                except (PlaybackError, discord.ClientException, OSError) as e:
                    failures += 1
                    log.error("Сервер %s: [%d] не удалось проиграть %s: %s", guild.name, number, path.name, e)

                # При отключении от голоса плеер останавливается и звук «заканчивается»
                if not vc.is_connected():
                    reason = "бот отключён от голосового канала"
                    break
                if failures >= MAX_FAILURES_IN_ROW:
                    reason = f"{failures} ошибок воспроизведения подряд"
                    break
                if failures:
                    continue

                pause = random.uniform(playback.pause_min, playback.pause_max)
                log.info("Сервер %s: [%d] пауза %.1f с", guild.name, number, pause)
                await self._sleep(pause)
        except asyncio.CancelledError:
            reason = "остановлена"
            if vc.is_playing():
                vc.stop()
            raise
        except Exception:
            reason = "ошибка"
            log.exception("Сервер %s: ошибка в сессии", guild.name)
        finally:
            if self._tasks.get(guild.id) is asyncio.current_task():
                del self._tasks[guild.id]
            log.info("Сервер %s: сессия завершена (%s), звуков: %d", guild.name, reason, number)


def _log_choice_table(guild: discord.Guild, choice: Choice) -> None:
    if not log.isEnabledFor(logging.DEBUG):
        return
    lines = [f"Сервер {guild.name}: выбор звука, окно {choice.window}"]
    lines.append(f"  {'звук':<40} {'вес':>5} {'множ.':>6} {'шанс':>6}  разрешён")
    for row in choice.rows:
        lines.append(
            f"  {row.name:<40} {row.base_weight:>5.2f} {row.multiplier:>6.2f} "
            f"{row.chance * 100:>5.0f}%  {'да' if row.allowed else 'нет'}"
        )
    log.debug("\n".join(lines))
