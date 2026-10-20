"""Выбор следующего звука: динамические веса и ограничение повторов.

Модуль не зависит от Discord и asyncio. Состояние (множители и история)
хранится в объекте SoundSelector — по одному на сервер.

Алгоритм перед каждым звуком:
1. Синхронизация с папкой: новые файлы получают множитель 1.0, удалённые убираются.
2. Кандидаты — звуки с базовым весом > 0, их N штук.
3. Эффективное окно W = max(1, min(window, (N − 2) × max_repeats + 1)).
4. Звук запрещён, если среди последних W − 1 проигранных он встречается
   max_repeats раз или больше. Заблокированных не больше (W − 1) / max_repeats ≤ N − 2,
   поэтому всегда разрешено минимум 2 звука (при N ≥ 2).

   Почему не min(window, N × max_repeats): при таком W в каждом окне каждый звук
   обязан встречаться ровно max_repeats раз, разрешён ровно один звук, и
   последовательность становится циклом длины W, а базовые веса перестают влиять.
5. Выбор среди разрешённых с вероятностью ∝ базовый вес × множитель
   (равновероятно, если все эффективные веса нулевые).
6. Выбранный получает множитель after_play, остальные растут на growth до max_weight.
"""

from __future__ import annotations

import random
from collections import Counter, deque
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import SelectionConfig, WeightsConfig


@dataclass(frozen=True)
class SoundRow:
    """Состояние одного звука в момент выбора (для отладочного лога)."""

    name: str
    base_weight: float
    multiplier: float
    allowed: bool
    chance: float


@dataclass(frozen=True)
class Choice:
    path: Path
    # Вероятность, с которой был выбран этот звук (0..1)
    chance: float
    # Эффективное окно W
    window: int
    rows: tuple[SoundRow, ...]
    # Имена из [weights], которых нет в папке
    unknown_weights: tuple[str, ...]


def effective_window(selection: SelectionConfig, candidates: int) -> int:
    """Окно W, при котором всегда остаётся минимум 2 разрешённых звука."""
    return max(1, min(selection.window, (candidates - 2) * selection.max_repeats + 1))


class SoundSelector:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()
        self._multipliers: dict[str, float] = {}
        self._history: deque[str] = deque()

    @property
    def history(self) -> list[str]:
        return list(self._history)

    def multiplier(self, name: str) -> float:
        return self._multipliers.get(name, 1.0)

    def _sync(self, names: list[str]) -> None:
        present = set(names)
        for name in list(self._multipliers):
            if name not in present:
                del self._multipliers[name]
        for name in names:
            self._multipliers.setdefault(name, 1.0)

    def choose(
        self,
        files: Sequence[Path],
        selection: SelectionConfig,
        weights: WeightsConfig,
    ) -> Choice | None:
        """Выбирает следующий звук и обновляет состояние. None — выбирать не из чего."""
        files = sorted(files, key=lambda p: p.name)
        names = [p.name for p in files]
        self._sync(names)

        lower_names = {name.lower() for name in names}
        unknown = tuple(n for n in weights.names() if n.lower() not in lower_names)

        base = {p.name: weights.weight_for(p.name) for p in files}
        candidates = [p for p in files if base[p.name] > 0]
        if not candidates:
            return None

        window = effective_window(selection, len(candidates))
        recent = Counter(list(self._history)[-(window - 1):]) if window > 1 else Counter()
        allowed = [p for p in candidates if recent[p.name] < selection.max_repeats]

        effective = {p.name: base[p.name] * self._multipliers[p.name] for p in allowed}
        total = sum(effective.values())
        if total > 0:
            chances = {name: w / total for name, w in effective.items()}
            chosen = self._rng.choices(allowed, weights=[effective[p.name] for p in allowed])[0]
        else:
            chances = {p.name: 1 / len(allowed) for p in allowed}
            chosen = self._rng.choice(allowed)

        rows = tuple(
            SoundRow(
                name=name,
                base_weight=base[name],
                multiplier=self._multipliers[name],
                allowed=name in chances,
                chance=chances.get(name, 0.0),
            )
            for name in names
        )

        # Состояние обновляется до проигрывания: неудачный файл тоже считается сыгранным
        for name in self._multipliers:
            if name == chosen.name:
                self._multipliers[name] = selection.after_play
            else:
                self._multipliers[name] = min(self._multipliers[name] + selection.growth, selection.max_weight)
        self._history.append(chosen.name)
        while len(self._history) > selection.window:
            self._history.popleft()

        return Choice(
            path=chosen,
            chance=chances[chosen.name],
            window=window,
            rows=rows,
            unknown_weights=unknown,
        )
