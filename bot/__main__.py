"""Точка входа: python -m bot"""

from __future__ import annotations

import logging
import os
import sys

import aiohttp
import discord
from dotenv import load_dotenv

from .checks import run_startup_checks
from .client import SoundBot
from .paths import ENV_PATH

log = logging.getLogger("bot")


def main() -> int:
    # При перенаправлении вывода в файл Windows по умолчанию берёт ANSI-кодировку
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    load_dotenv(ENV_PATH)
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        log.error("DISCORD_TOKEN не задан. Скопируйте .env.example в .env и впишите токен.")
        return 1

    guild_id_raw = os.getenv("GUILD_ID", "").strip()
    guild_id: int | None = None
    if guild_id_raw:
        if not guild_id_raw.isdigit():
            log.error("GUILD_ID должен быть числом (ID сервера), получено: %r", guild_id_raw)
            return 1
        guild_id = int(guild_id_raw)

    run_startup_checks()

    bot = SoundBot(guild_id=guild_id)
    # log_handler=None: логирование уже настроено выше
    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        log.error("Discord отклонил токен. Проверьте DISCORD_TOKEN в .env.")
        return 1
    except (aiohttp.ClientConnectionError, OSError) as e:
        log.error("Нет связи с Discord: %s. Проверьте, что VPN включён.", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
