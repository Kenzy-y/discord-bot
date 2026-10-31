"""Симулятор выбора звуков: прогоняет селектор без Discord и без пауз.

Запуск из корня репозитория:
    python tools/simulate_selection.py
    python tools/simulate_selection.py -n 5000 --seed 1
    python tools/simulate_selection.py --sounds путь/к/папке --config путь/к/config.toml
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import ConfigError, load_config  # noqa: E402
from bot.paths import CONFIG_PATH, SOUNDS_DIR  # noqa: E402
from bot.selector import SoundSelector  # noqa: E402
from bot.sounds import list_all_sounds  # noqa: E402


def max_in_any_window(picked: list[str], window: int) -> int:
    worst = 0
    counts: Counter[str] = Counter()
    for i, name in enumerate(picked):
        counts[name] += 1
        if i >= window:
            counts[picked[i - window]] -= 1
        worst = max(worst, counts[name])
    return worst


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")

    parser = argparse.ArgumentParser(description="Симуляция выбора звуков без Discord")
    parser.add_argument("-n", "--iterations", type=int, default=1000, help="число выборов (по умолчанию 1000)")
    parser.add_argument("--sounds", type=Path, default=SOUNDS_DIR, help="папка со звуками")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="путь к config.toml")
    parser.add_argument("--seed", type=int, default=None, help="seed генератора для повторяемого результата")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("число итераций должно быть не меньше 1")

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Ошибка в конфиге: {e}", file=sys.stderr)
        return 1
    files = list_all_sounds(args.sounds)
    if not files:
        print(f"В папке {args.sounds} нет звуков (.ogg, .opus, .mp3, .wav).", file=sys.stderr)
        return 1

    selector = SoundSelector(random.Random(args.seed))
    picked: list[str] = []
    window = 0
    unknown: tuple[str, ...] = ()
    for _ in range(args.iterations):
        choice = selector.choose(files, config.selection, config.weights)
        if choice is None:
            print("У всех звуков вес 0 — выбирать не из чего.", file=sys.stderr)
            return 1
        picked.append(choice.path.name)
        window, unknown = choice.window, choice.unknown_weights

    sel = config.selection
    total = len(picked)
    counts = Counter(picked)
    playable = [p for p in files if config.weights.weight_for(p.name) > 0]

    print(f"Звуков в папке: {len(files)}, участвуют в выборе (вес > 0): {len(playable)}")
    print(
        f"Настройки: after_play={sel.after_play:g}, growth={sel.growth:g}, max_weight={sel.max_weight:g}, "
        f"window={sel.window}, max_repeats={sel.max_repeats}"
    )
    if unknown:
        print(f"Внимание: в [weights] есть звуки, которых нет в папке: {', '.join(unknown)}")
    print(f"Итераций: {total}")
    print()

    width = max(len(p.name) for p in files)
    print(f"{'Звук':<{width}}  {'вес':>5}  {'раз':>6}  {'доля':>6}")
    for p in sorted(files, key=lambda p: -counts[p.name]):
        print(
            f"{p.name:<{width}}  {config.weights.weight_for(p.name):>5.2f}  "
            f"{counts[p.name]:>6}  {counts[p.name] / total:>6.1%}"
        )
    print()

    if total > 1:
        repeats = sum(a == b for a, b in zip(picked, picked[1:]))
        uniform = f" (при равновероятном выборе было бы ~{1 / len(playable):.1%})" if len(playable) > 1 else ""
        print(f"Повтор того же звука сразу подряд: {repeats / (total - 1):.1%} случаев{uniform}")

    if window < sel.window:
        print(f"Эффективное окно W: {window} (сужено с {sel.window}, потому что звуков мало)")
    else:
        print(f"Эффективное окно W: {window}")
    worst = max_in_any_window(picked, window)
    verdict = "в норме" if worst <= sel.max_repeats else "НАРУШЕНИЕ"
    print(f"Максимум повторов одного звука в любом окне из {window}: {worst} (лимит {sel.max_repeats}, {verdict})")
    return 0 if worst <= sel.max_repeats else 2


if __name__ == "__main__":
    sys.exit(main())
