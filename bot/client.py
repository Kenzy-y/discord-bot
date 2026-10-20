from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import discord
from discord import app_commands

from .commands import register_commands
from .session import SessionManager

log = logging.getLogger(__name__)


class SoundBot(discord.Client):
    def __init__(self, *, guild_id: int | None) -> None:
        # Только непривилегированные intents: серверы и голосовые состояния
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.sessions = SessionManager()
        self.guild_id = guild_id
        self._voice_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        register_commands(self)

    def voice_lock(self, guild_id: int) -> asyncio.Lock:
        return self._voice_locks[guild_id]

    async def setup_hook(self) -> None:
        if self.guild_id is not None:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Слэш-команды синхронизированы на сервер %s: %d", self.guild_id, len(synced))
        else:
            synced = await self.tree.sync()
            log.info("Слэш-команды синхронизированы глобально: %d (появление может занять до часа)", len(synced))

    async def on_ready(self) -> None:
        log.info("Бот запущен как %s (id %s), серверов: %d", self.user, self.user.id, len(self.guilds))

    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if self.user is None or member.id != self.user.id:
            return
        if before.channel is not None and after.channel is None:
            log.info("Сервер %s: бот вышел из голосового канала %s", member.guild.name, before.channel.name)
            self.sessions.cancel(member.guild.id)
        elif after.channel is not None and before.channel != after.channel:
            log.info("Сервер %s: бот в канале %s", member.guild.name, after.channel.name)

    async def close(self) -> None:
        await self.sessions.stop_all()
        await super().close()
