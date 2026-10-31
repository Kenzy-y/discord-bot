"""Тесты выбора звуков. Генератор с фиксированным seed — результаты детерминированы."""

import random
from collections import Counter
from pathlib import Path

import pytest

from bot.config import SelectionConfig, WeightsConfig
from bot.selector import SoundSelector

NO_WEIGHTS = WeightsConfig(entries={})


def files(n):
    return [Path(f"s{i:02d}.ogg") for i in range(n)]


def weights(**values):
    return WeightsConfig(entries={k.lower(): (k, float(v)) for k, v in values.items()})


def run(selector, sounds, selection, weights_config=NO_WEIGHTS, count=10_000):
    picked = []
    playable = sum(weights_config.weight_for(p.name) > 0 for p in sounds)
    for _ in range(count):
        choice = selector.choose(sounds, selection, weights_config)
        assert choice is not None, "нет разрешённых звуков"
        allowed = sum(row.allowed for row in choice.rows)
        # Окно подбирается так, чтобы выбор никогда не был вынужденным
        assert allowed >= min(2, playable), choice.rows
        picked.append(choice.path.name)
    return picked


def expected_window(window, max_repeats, n):
    return max(1, min(window, (n - 2) * max_repeats + 1))


def max_in_any_window(picked, window):
    worst = 0
    counts = Counter()
    for i, name in enumerate(picked):
        counts[name] += 1
        if i >= window:
            counts[picked[i - window]] -= 1
        worst = max(worst, counts[name])
    return worst


@pytest.mark.parametrize("n", [1, 2, 3, 5, 10])
@pytest.mark.parametrize(
    ("window", "max_repeats"),
    [(10, 2), (10, 1), (5, 3), (1, 1), (3, 1), (20, 4), (7, 2)],
)
def test_window_limit_never_violated(n, window, max_repeats):
    selection = SelectionConfig(window=window, max_repeats=max_repeats)
    picked = run(SoundSelector(random.Random(n * 100 + window)), files(n), selection)
    effective = expected_window(window, max_repeats, n)
    assert max_in_any_window(picked, effective) <= max_repeats
    assert len(set(picked)) == n


def test_small_n_is_not_a_fixed_cycle():
    # Регрессия: при W = N × max_repeats (формула из исходной задачи) 3 звука
    # после разгона шли строгим циклом длины 6: picked[i] == picked[i - 6]
    picked = run(SoundSelector(random.Random(11)), files(3), SelectionConfig(), count=1000)
    for period in range(1, 13):
        same = sum(picked[i] == picked[i - period] for i in range(period, len(picked)))
        assert same / (len(picked) - period) < 0.7, period


@pytest.mark.parametrize("n", [1, 2, 3, 5])
def test_never_stuck_with_zero_weights(n):
    # after_play = 0, growth = 0: после первого круга все эффективные веса нулевые —
    # выбор должен стать равновероятным среди разрешённых, а не падать
    selection = SelectionConfig(after_play=0.0, growth=0.0, max_weight=1.0, window=10, max_repeats=1)
    picked = run(SoundSelector(random.Random(n)), files(n), selection, count=2_000)
    assert max_in_any_window(picked, expected_window(10, 1, n)) <= 1
    assert len(set(picked)) == n


def test_single_sound_with_after_play_zero_still_plays():
    selection = SelectionConfig(after_play=0.0)
    selector = SoundSelector(random.Random(0))
    for _ in range(5):
        choice = selector.choose(files(1), selection, NO_WEIGHTS)
        assert choice.path.name == "s00.ogg"
        assert choice.chance == 1.0


def immediate_repeat_rate(picked):
    repeats = sum(a == b for a, b in zip(picked, picked[1:]))
    return repeats / (len(picked) - 1)


def test_dynamic_weights_reduce_immediate_repeats():
    picked = run(SoundSelector(random.Random(42)), files(3), SelectionConfig())
    rate = immediate_repeat_rate(picked)
    assert rate < 0.1, rate  # при равновероятном выборе было бы ~1/3
    counts = Counter(picked)
    assert all(3000 < c < 3700 for c in counts.values()), counts


def test_without_dynamic_weights_repeats_are_frequent():
    # Контроль: без динамических весов и без окна повторы подряд — около 1/3
    selection = SelectionConfig(after_play=1.0, growth=0.0, max_weight=1.0, window=1, max_repeats=1)
    rate = immediate_repeat_rate(run(SoundSelector(random.Random(42)), files(3), selection))
    assert 0.3 < rate < 0.37, rate


def test_base_weights():
    sounds = files(4)
    w = weights(**{"s00.ogg": 0.3, "s01.ogg": 0})
    counts = Counter(run(SoundSelector(random.Random(7)), sounds, SelectionConfig(), w))
    assert counts["s01.ogg"] == 0
    others = [counts["s02.ogg"], counts["s03.ogg"]]
    assert counts["s00.ogg"] < 0.7 * min(others), counts


def test_base_weight_names_case_insensitive():
    w = weights(**{"S00.OGG": 0})
    counts = Counter(run(SoundSelector(random.Random(8)), files(3), SelectionConfig(), w, count=1000))
    assert counts["s00.ogg"] == 0


def test_all_base_weights_zero_returns_none():
    w = weights(**{"s00.ogg": 0, "s01.ogg": 0})
    assert SoundSelector(random.Random(0)).choose(files(2), SelectionConfig(), w) is None


def test_added_sound_starts_playing_and_removed_stops():
    selector = SoundSelector(random.Random(5))
    selection = SelectionConfig()
    sounds = files(3)
    run(selector, sounds, selection, count=100)

    added = sounds + [Path("new.ogg")]
    picked = run(selector, added, selection, count=300)
    assert "new.ogg" in picked
    assert selector.multiplier("new.ogg") > 0

    removed = [p for p in added if p.name != "s00.ogg"]
    picked = run(selector, removed, selection, count=300)
    assert "s00.ogg" not in picked
    # Удалённый звук исчезает из состояния
    assert all(row.name != "s00.ogg" for row in selector.choose(removed, selection, NO_WEIGHTS).rows)


def test_multipliers_update():
    selection = SelectionConfig(after_play=0.1, growth=0.3, max_weight=1.5)
    selector = SoundSelector(random.Random(1))
    sounds = files(3)
    first = selector.choose(sounds, selection, NO_WEIGHTS).path.name
    assert selector.multiplier(first) == pytest.approx(0.1)
    for p in sounds:
        if p.name != first:
            assert selector.multiplier(p.name) == pytest.approx(1.3)
    second = selector.choose(sounds, selection, NO_WEIGHTS).path.name
    for p in sounds:
        if p.name not in (first, second):
            assert selector.multiplier(p.name) == pytest.approx(1.5)  # упёрся в max_weight
    assert selector.history == [first, second]


def test_chances_and_rows():
    selector = SoundSelector(random.Random(2))
    w = weights(**{"s00.ogg": 2, "s01.ogg": 0})
    choice = selector.choose(files(3), SelectionConfig(), w)
    by_name = {row.name: row for row in choice.rows}
    assert by_name["s00.ogg"].chance == pytest.approx(2 / 3)
    assert by_name["s01.ogg"].allowed is False
    assert sum(row.chance for row in choice.rows) == pytest.approx(1.0)
    assert choice.chance == by_name[choice.path.name].chance


@pytest.mark.parametrize(("n", "window"), [(1, 1), (2, 1), (3, 3), (5, 7), (10, 10), (30, 10)])
def test_effective_window(n, window):
    choice = SoundSelector(random.Random(3)).choose(files(n), SelectionConfig(window=10, max_repeats=2), NO_WEIGHTS)
    assert choice.window == window


def test_zero_weight_sounds_do_not_count_for_window():
    w = weights(**{"s00.ogg": 0, "s01.ogg": 0})
    choice = SoundSelector(random.Random(3)).choose(files(5), SelectionConfig(window=10, max_repeats=2), w)
    assert choice.window == 3


def test_unknown_weights():
    w = weights(**{"S01.ogg": 1.5, "ghost.ogg": 2})
    choice = SoundSelector(random.Random(3)).choose(files(3), SelectionConfig(), w)
    assert choice.unknown_weights == ("ghost.ogg",)
