"""Выбор голосового канала и подключение к нему."""

from __future__ import annotations

import asyncio
import logging
import random

import discord

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 20.0


class VoiceError(Exception):
    """Ошибка подключения. Текст пригоден для показа пользователю."""


def _missing_permissions(channel: discord.abc.GuildChannel, me: discord.Member) -> bool:
    perms = channel.permissions_for(me)
    return not (perms.view_channel and perms.connect and perms.speak)


def _is_full(channel: discord.VoiceChannel, me: discord.Member) -> bool:
    if not channel.user_limit:
        return False
    others = [m for m in channel.members if m.id != me.id]
    return len(others) >= channel.user_limit


def pick_random_channel(guild: discord.Guild) -> discord.VoiceChannel | None:
    me = guild.me
    afk_id = guild.afk_channel.id if guild.afk_channel else None
    candidates = [
        ch for ch in guild.voice_channels
        if ch.id != afk_id and not _missing_permissions(ch, me) and not _is_full(ch, me)
    ]
    return random.choice(candidates) if candidates else None


def resolve_target(guild: discord.Guild, member: discord.Member) -> discord.abc.Connectable:
    """Канал автора команды, если он в голосе, иначе случайный подходящий."""
    if member.voice and member.voice.channel:
        channel = member.voice.channel
        if _missing_permissions(channel, guild.me):
            raise VoiceError(f"У меня нет прав подключиться и говорить в **{channel.name}**.")
        return channel
    channel = pick_random_channel(guild)
    if channel is None:
        raise VoiceError("Нет подходящих голосовых каналов.")
    return channel


async def connect_to(guild: discord.Guild, channel: discord.abc.Connectable) -> discord.VoiceClient:
    """Подключается к каналу или перемещается в него, если уже подключён на сервере."""
    vc = guild.voice_client
    try:
        if isinstance(vc, discord.VoiceClient) and vc.is_connected():
            if vc.channel.id != channel.id:
                log.info("Сервер %s: перемещаюсь в канал %s", guild.name, channel.name)
                await vc.move_to(channel, timeout=CONNECT_TIMEOUT)
            return vc
        if vc is not None:
            # Подвисшее подключение — убираем, чтобы не мешало новому
            await vc.disconnect(force=True)
        log.info("Сервер %s: подключаюсь к каналу %s", guild.name, channel.name)
        vc = await channel.connect(timeout=CONNECT_TIMEOUT, self_deaf=True)
        log.info("Сервер %s: подключён к каналу %s", guild.name, channel.name)
        return vc
    except asyncio.TimeoutError:
        log.warning("Сервер %s: таймаут подключения к каналу %s", guild.name, channel.name)
        raise VoiceError(
            "Не удалось подключиться к голосовому каналу: таймаут. "
            "Проверьте VPN — голос Discord идёт по UDP."
        ) from None
    except discord.ConnectionClosed as e:
        log.warning("Сервер %s: голосовое соединение закрыто с кодом %s", guild.name, e.code)
        if e.code == 4017:
            raise VoiceError("Discord отклонил подключение (4017): нужен DAVE, проверьте пакет davey.") from None
        raise VoiceError(f"Голосовое соединение закрыто (код {e.code}).") from None
    except (discord.ClientException, RuntimeError) as e:
        log.warning("Сервер %s: ошибка подключения: %s", guild.name, e)
        raise VoiceError(f"Не удалось подключиться: {e}") from None
