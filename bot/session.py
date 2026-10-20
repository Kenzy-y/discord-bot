"""Сессии случайного воспроизведения: одна фоновая задача на сервер."""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import discord

from .checks import probe_method
from .config import PlaybackConfig

log = logging.getLogger(__name__)


class SessionManager:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def is_running(self, guild_id: int) -> bool:
        task = self._tasks.get(guild_id)
        return task is not None and not task.done()

    def start(
        self,
        guild: discord.Guild,
        vc: discord.VoiceClient,
        config: PlaybackConfig,
        files: list[Path],
    ) -> int:
        """Запускает сессию и возвращает число звуков, которое будет проиграно."""
        plays = random.randint(config.plays_min, config.plays_max)
        task = asyncio.create_task(
            self._run(guild, vc, config, files, plays), name=f"session-{guild.id}"
        )
        self._tasks[guild.id] = task
        return plays

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

    async def _run(
        self,
        guild: discord.Guild,
        vc: discord.VoiceClient,
        config: PlaybackConfig,
        files: list[Path],
        plays: int,
    ) -> None:
        log.info("Сервер %s: сессия началась, звуков: %d", guild.name, plays)
        reason = "все звуки проиграны"
        try:
            for i in range(plays):
                path = random.choice(files)
                log.info("Сервер %s: [%d/%d] играю %s", guild.name, i + 1, plays, path.name)
                try:
                    await self._play(vc, path)
                except (discord.ClientException, OSError) as e:
                    log.error("Сервер %s: не удалось проиграть %s: %s", guild.name, path.name, e)
                # При отключении от голоса плеер останавливается и звук «заканчивается»
                if not vc.is_connected():
                    reason = "бот отключён от голосового канала"
                    break
                if i < plays - 1:
                    pause = random.uniform(config.pause_min, config.pause_max)
                    await asyncio.sleep(pause)
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
            log.info("Сервер %s: сессия завершена (%s)", guild.name, reason)

    async def _play(self, vc: discord.VoiceClient, path: Path) -> None:
        """Проигрывает файл до конца. Opus-файлы идут без перекодирования."""
        loop = asyncio.get_running_loop()
        finished: asyncio.Future[Exception | None] = loop.create_future()

        def resolve(error: Exception | None) -> None:
            if not finished.done():
                finished.set_result(error)

        def after(error: Exception | None) -> None:
            # Вызывается из потока плеера
            loop.call_soon_threadsafe(resolve, error)

        source = await discord.FFmpegOpusAudio.from_probe(str(path), method=probe_method())
        try:
            vc.play(source, after=after)
        except Exception:
            source.cleanup()
            raise
        error = await finished
        if error is not None:
            log.error("Ошибка воспроизведения %s: %s", path.name, error)
