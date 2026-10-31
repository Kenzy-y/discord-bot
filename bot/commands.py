"""Слэш-команды бота."""

from __future__ import annotations

import logging
from typing import Literal, TYPE_CHECKING

import discord
from discord import app_commands

from .checks import ffmpeg_available
from .config import ConfigError, ConfigReloader
from .paths import CONFIG_PATH, SOUNDS_DIR
from .sounds import get_sounds_by_type, list_all_sounds
from .voice import VoiceError, connect_to, resolve_target

if TYPE_CHECKING:
    from .client import SoundBot

log = logging.getLogger(__name__)


async def reply_error(interaction: discord.Interaction, text: str) -> None:
    """Ephemeral-ответ об ошибке, в том числе после defer."""
    if not interaction.response.is_done():
        await interaction.response.send_message(text, ephemeral=True)
        return
    # Первый followup после публичного defer наследует его видимость,
    # поэтому убираем «думает…» и отправляем ошибку отдельным сообщением.
    try:
        await interaction.delete_original_response()
    except discord.HTTPException:
        pass
    await interaction.followup.send(text, ephemeral=True)


async def _connect(
    bot: SoundBot, guild: discord.Guild, target: discord.abc.Connectable
) -> discord.VoiceClient:
    async with bot.voice_lock(guild.id):
        return await connect_to(guild, target)


def register_commands(bot: SoundBot) -> None:
    tree = bot.tree

    @tree.command(name="connect", description="Подключиться к голосовому каналу")
    @app_commands.guild_only()
    async def connect(interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None and isinstance(interaction.user, discord.Member)
        try:
            # Канал выбираем до defer, чтобы ошибка была ephemeral без лишних сообщений
            target = resolve_target(guild, interaction.user)
            await interaction.response.defer(thinking=True)
            vc = await _connect(bot, guild, target)
        except VoiceError as e:
            await reply_error(interaction, str(e))
            return
        await interaction.followup.send(f"Подключился к **{vc.channel.name}**.")

    @tree.command(name="start", description="Проигрывать случайные звуки, пока не остановят")
    @app_commands.guild_only()
    async def start(interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None and isinstance(interaction.user, discord.Member)

        if bot.sessions.is_running(guild.id):
            await reply_error(interaction, "Уже играю.")
            return
        config = ConfigReloader(CONFIG_PATH)
        try:
            config.load()
        except ConfigError as e:
            log.error("Ошибка конфига: %s", e)
            await reply_error(interaction, f"Ошибка в config.toml: {e}")
            return
        files = list_all_sounds(SOUNDS_DIR)
        if not files:
            await reply_error(interaction, "В папке sounds/ нет звуков.")
            return
        sounds_by_type = get_sounds_by_type(SOUNDS_DIR)
        mode = bot.sessions.get_mode(guild.id)
        if mode == "short" and "short" not in sounds_by_type:
            await reply_error(interaction, "В папке sounds/short/ нет звуков.")
            return
        if mode == "long" and "long" not in sounds_by_type:
            await reply_error(interaction, "В папке sounds/long/ нет звуков.")
            return
        if not ffmpeg_available():
            await reply_error(interaction, "FFmpeg не найден на компьютере бота.")
            return

        try:
            vc = guild.voice_client
            if not (isinstance(vc, discord.VoiceClient) and vc.is_connected()):
                target = resolve_target(guild, interaction.user)
                await interaction.response.defer(thinking=True)
                vc = await _connect(bot, guild, target)
            else:
                await interaction.response.defer(thinking=True)
        except VoiceError as e:
            await reply_error(interaction, str(e))
            return

        # Пока подключались, сессию мог запустить кто-то другой
        if bot.sessions.is_running(guild.id):
            await reply_error(interaction, "Уже играю.")
            return
        bot.sessions.start(guild, vc, config)
        await interaction.followup.send(f"Играю в **{vc.channel.name}**. Остановить — /stop.")

    @tree.command(name="stop", description="Остановить воспроизведение")
    @app_commands.guild_only()
    async def stop(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        if not await bot.sessions.stop(interaction.guild.id):
            await reply_error(interaction, "Сейчас ничего не играет.")
            return
        await interaction.response.send_message("Остановлено.")

    @tree.command(name="leave", description="Остановить воспроизведение и выйти из голосового канала")
    @app_commands.guild_only()
    async def leave(interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        vc = guild.voice_client
        if vc is None:
            await bot.sessions.stop(guild.id)
            await reply_error(interaction, "Я не в голосовом канале.")
            return
        await interaction.response.defer(thinking=True)
        await bot.sessions.stop(guild.id)
        async with bot.voice_lock(guild.id):
            await vc.disconnect(force=True)
        log.info("Сервер %s: отключился от голосового канала", guild.name)
        await interaction.followup.send("Отключился.")

    @tree.command(name="mode", description="Переключить режим звуков: short, long или both")
    @app_commands.describe(mode="Режим: short (короткие), long (длинные) или both (все)")
    @app_commands.guild_only()
    async def mode_cmd(
        interaction: discord.Interaction,
        mode: Literal["both", "short", "long"] | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        mode_labels = {
            "both": "все звуки (both: short + long)",
            "short": "только короткие (short)",
            "long": "только длинные (long)",
        }
        if mode is None:
            current = bot.sessions.get_mode(guild.id)
            label = mode_labels.get(current, current)
            await interaction.response.send_message(f"Текущий режим: **{label}**.")
            return

        bot.sessions.set_mode(guild.id, mode)
        sounds_by_type = get_sounds_by_type(SOUNDS_DIR)
        extra = ""
        if mode == "long" and "long" not in sounds_by_type:
            extra = "\n⚠️ *В папке sounds/long/ пока нет звуковых файлов.*"
        elif mode == "short" and "short" not in sounds_by_type:
            extra = "\n⚠️ *В папке sounds/short/ пока нет звуковых файлов.*"

        log.info("Сервер %s: режим звуков переключён на %s", guild.name, mode)
        await interaction.response.send_message(f"Режим переключён на: **{mode_labels[mode]}**.{extra}")

    @tree.error
    async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        name = interaction.command.name if interaction.command else "?"
        log.error("Ошибка в команде /%s", name, exc_info=error)
        try:
            await reply_error(interaction, "Что-то пошло не так, подробности в логе бота.")
        except discord.HTTPException:
            pass
