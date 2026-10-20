"""Загрузка и валидация config.toml.

Каждая секция конфига — отдельный dataclass с методом from_table.
Чтобы добавить новую секцию: описать dataclass, добавить поле в Config
и строчку в parse_config. Неизвестные секции и параметры игнорируются.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ConfigError(Exception):
    """Ошибка чтения или валидации конфига. Текст пригоден для показа пользователю."""


_MISSING = object()


def _get_table(data: dict[str, Any], name: str, *, optional: bool = False) -> dict[str, Any]:
    if optional and name not in data:
        return {}
    table = data.get(name)
    if not isinstance(table, dict):
        if name in data:
            raise ConfigError(f"[{name}] в config.toml должно быть секцией")
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

    def number(self, key: str, *, integer: bool = False, minimum: float = 0, default: Any = _MISSING) -> Any:
        if key not in self.table:
            if default is not _MISSING:
                return default
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

    def error(self, message: str) -> None:
        self.errors.append(message)

    def raise_if_errors(self) -> None:
        if self.errors:
            raise ConfigError("; ".join(self.errors))


@dataclass(frozen=True)
class PlaybackConfig:
    pause_min: float
    pause_max: float

    @classmethod
    def from_table(cls, table: dict[str, Any]) -> PlaybackConfig:
        r = _Reader("playback", table)
        pause_min = r.number("pause_min")
        pause_max = r.number("pause_max")
        r.range("pause_min", "pause_max", pause_min, pause_max)
        r.raise_if_errors()
        return cls(pause_min=float(pause_min), pause_max=float(pause_max))


@dataclass(frozen=True)
class SelectionConfig:
    """Настройки выбора следующего звука (см. bot/selector.py)."""

    after_play: float = 0.1
    growth: float = 0.3
    max_weight: float = 3.0
    window: int = 10
    max_repeats: int = 2

    @classmethod
    def from_table(cls, table: dict[str, Any]) -> SelectionConfig:
        d = cls()
        r = _Reader("selection", table)
        after_play = r.number("after_play", default=d.after_play)
        growth = r.number("growth", default=d.growth)
        max_weight = r.number("max_weight", minimum=1.0, default=d.max_weight)
        window = r.number("window", integer=True, minimum=1, default=d.window)
        max_repeats = r.number("max_repeats", integer=True, minimum=1, default=d.max_repeats)
        r.range("after_play", "max_weight", after_play, max_weight)
        r.raise_if_errors()
        return cls(
            after_play=float(after_play),
            growth=float(growth),
            max_weight=float(max_weight),
            window=window,
            max_repeats=max_repeats,
        )


@dataclass(frozen=True)
class WeightsConfig:
    """Базовые веса звуков по имени файла. Звуки, которых здесь нет, имеют вес 1.0."""

    # Ключ — имя файла в нижнем регистре, значение — (имя как в конфиге, вес)
    entries: dict[str, tuple[str, float]]

    def weight_for(self, filename: str) -> float:
        entry = self.entries.get(filename.lower())
        return 1.0 if entry is None else entry[1]

    def names(self) -> list[str]:
        """Имена из конфига в исходном написании."""
        return [name for name, _ in self.entries.values()]

    @classmethod
    def from_table(cls, table: dict[str, Any]) -> WeightsConfig:
        r = _Reader("weights", table)
        entries: dict[str, tuple[str, float]] = {}
        for name, value in table.items():
            if isinstance(value, dict):
                # rare.ogg = 0.3 без кавычек TOML читает как вложенную таблицу
                example = f"{name}.{next(iter(value), 'ogg')}"
                r.error(f'weights: имя файла нужно писать в кавычках: "{example}" = …')
                continue
            weight = r.number(name)
            if weight is None:
                continue
            key = name.lower()
            if key in entries:
                r.error(f'weights: "{entries[key][0]}" и "{name}" — один и тот же файл (регистр не учитывается)')
                continue
            entries[key] = (name, float(weight))
        r.raise_if_errors()
        return cls(entries=entries)


@dataclass(frozen=True)
class Config:
    playback: PlaybackConfig
    selection: SelectionConfig = SelectionConfig()
    weights: WeightsConfig = WeightsConfig(entries={})


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise ConfigError(f"файл {path.name} не найден") from None
    except OSError as e:
        raise ConfigError(f"не удалось прочитать {path.name}: {e}") from None


def parse_config(raw: bytes, name: str = "config.toml") -> Config:
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise ConfigError(f"{name} должен быть в кодировке UTF-8") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"синтаксическая ошибка в {name}: {e}") from None

    # [selection] и [weights] необязательны: старые конфиги без них работают
    return Config(
        playback=PlaybackConfig.from_table(_get_table(data, "playback")),
        selection=SelectionConfig.from_table(_get_table(data, "selection", optional=True)),
        weights=WeightsConfig.from_table(_get_table(data, "weights", optional=True)),
    )


def load_config(path: Path) -> Config:
    return parse_config(_read_bytes(path), path.name)


class ConfigReloader:
    """Перечитывает конфиг по ходу сессии.

    Если файл стал некорректным, остаются последние корректные значения,
    а предупреждение пишется в лог один раз на каждое изменение файла.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._raw: bytes | None = None
        self._config: Config | None = None

    def load(self) -> Config:
        """Первая загрузка: при ошибке бросает ConfigError."""
        raw = _read_bytes(self.path)
        self._config = parse_config(raw, self.path.name)
        self._raw = raw
        return self._config

    def get(self) -> Config:
        """Актуальный корректный конфиг. Не бросает исключений после успешного load()."""
        assert self._config is not None, "сначала нужно вызвать load()"
        try:
            raw: bytes | None = _read_bytes(self.path)
            error: ConfigError | None = None
        except ConfigError as e:
            raw, error = None, e
        if raw == self._raw:
            return self._config
        self._raw = raw

        if raw is not None:
            try:
                config = parse_config(raw, self.path.name)
            except ConfigError as e:
                error = e
            else:
                if config != self._config:
                    log.info("%s перечитан: %s", self.path.name, config)
                self._config = config
                return config
        log.warning("Ошибка в %s, продолжаю с прежними настройками: %s", self.path.name, error)
        return self._config
