"""Загрузка и валидация config.toml.

Каждая секция конфига — отдельный dataclass с методом from_table.
Чтобы добавить новую секцию: описать dataclass, добавить поле в Config
и строчку в load_config.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Ошибка чтения или валидации конфига. Текст пригоден для показа пользователю."""


def _get_table(data: dict[str, Any], name: str) -> dict[str, Any]:
    table = data.get(name)
    if not isinstance(table, dict):
        raise ConfigError(f"в config.toml нет секции [{name}]")
    return table


class _Reader:
    """Читает значения из секции и копит ошибки, чтобы показать их все разом."""

    def __init__(self, section: str, table: dict[str, Any]) -> None:
        self.section = section
        self.table = table
        self.errors: list[str] = []

    def _name(self, key: str) -> str:
        return f"{self.section}.{key}"

    def number(self, key: str, *, integer: bool = False, minimum: float = 0) -> Any:
        if key not in self.table:
            self.errors.append(f"{self._name(key)}: параметр не задан")
            return None
        value = self.table[key]
        allowed = (int,) if integer else (int, float)
        # bool — подкласс int, его отсекаем явно
        if isinstance(value, bool) or not isinstance(value, allowed):
            kind = "целым числом" if integer else "числом"
            self.errors.append(f"{self._name(key)}: должно быть {kind}, получено {value!r}")
            return None
        if value < minimum:
            self.errors.append(f"{self._name(key)}: должно быть не меньше {minimum}, получено {value}")
            return None
        return value

    def range(self, min_key: str, max_key: str, low: Any, high: Any) -> None:
        if low is not None and high is not None and low > high:
            self.errors.append(
                f"{self._name(min_key)} ({low}) больше {self._name(max_key)} ({high})"
            )

    def raise_if_errors(self) -> None:
        if self.errors:
            raise ConfigError("; ".join(self.errors))


@dataclass(frozen=True)
class PlaybackConfig:
    pause_min: float
    pause_max: float
    plays_min: int
    plays_max: int

    @classmethod
    def from_table(cls, table: dict[str, Any]) -> PlaybackConfig:
        r = _Reader("playback", table)
        pause_min = r.number("pause_min")
        pause_max = r.number("pause_max")
        plays_min = r.number("plays_min", integer=True, minimum=1)
        plays_max = r.number("plays_max", integer=True, minimum=1)
        r.range("pause_min", "pause_max", pause_min, pause_max)
        r.range("plays_min", "plays_max", plays_min, plays_max)
        r.raise_if_errors()
        return cls(
            pause_min=float(pause_min),
            pause_max=float(pause_max),
            plays_min=plays_min,
            plays_max=plays_max,
        )


@dataclass(frozen=True)
class Config:
    playback: PlaybackConfig


def load_config(path: Path) -> Config:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"файл {path.name} не найден") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"синтаксическая ошибка в {path.name}: {e}") from None
    except OSError as e:
        raise ConfigError(f"не удалось прочитать {path.name}: {e}") from None

    return Config(
        playback=PlaybackConfig.from_table(_get_table(data, "playback")),
    )
