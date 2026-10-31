"""Тесты сессии на моке голосового клиента: без Discord, FFmpeg и реальных пауз."""

import asyncio
import logging
import random
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import ConfigReloader
from bot.selector import SoundSelector
from bot.session import MAX_FAILURES_IN_ROW, PlaybackError, SessionManager

GUILD = SimpleNamespace(id=1, name="test")


class FakeVoiceClient:
    def __init__(self):
        self.connected = True
        self.playing = False
        self.stopped = 0

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.playing

    def stop(self):
        self.stopped += 1
        self.playing = False


class Recorder:
    """Подменяет воспроизведение и паузы, записывая события по порядку."""

    def __init__(self, fail=lambda path: False):
        self.events = []
        self.fail = fail

    @property
    def played(self):
        return [path.name for kind, path in self.events if kind == "play"]

    @property
    def pauses(self):
        return [value for kind, value in self.events if kind == "sleep"]

    async def player(self, vc, path):
        self.events.append(("play", path))
        await asyncio.sleep(0)
        if self.fail(path):
            raise PlaybackError("битый файл")

    async def sleep(self, seconds):
        self.events.append(("sleep", seconds))
        await asyncio.sleep(0)


@pytest.fixture
def sounds_dir(tmp_path):
    d = tmp_path / "sounds"
    d.mkdir()
    for name in ("a.ogg", "b.mp3", "c.wav"):
        (d / name).write_bytes(b"x")
    return d


def make_reloader(tmp_path, pause_min=1, pause_max=10, extra=""):
    path = tmp_path / "config.toml"
    path.write_text(
        f"[playback]\npause_min = {pause_min}\npause_max = {pause_max}\n{extra}", encoding="utf-8"
    )
    reloader = ConfigReloader(path)
    reloader.load()
    return reloader


def make_manager(sounds_dir, recorder, seed=None):
    factory = SoundSelector if seed is None else (lambda: SoundSelector(random.Random(seed)))
    return SessionManager(
        sounds_dir=sounds_dir, player=recorder.player, sleep=recorder.sleep, selector_factory=factory
    )


async def wait_until(condition, limit=100_000):
    for _ in range(limit):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("условие не выполнилось")


def test_regression_random_sounds_and_pauses(tmp_path, sounds_dir):
    """Регрессия: за много итераций выбираются разные звуки и разные паузы."""
    rec = Recorder()

    async def scenario():
        manager = make_manager(sounds_dir, rec)
        manager.start(GUILD, FakeVoiceClient(), make_reloader(tmp_path, 1, 10))
        await wait_until(lambda: len(rec.played) >= 300)
        assert await manager.stop(GUILD.id)

    asyncio.run(scenario())

    counts = Counter(rec.played)
    assert set(counts) == {"a.ogg", "b.mp3", "c.wav"}
    # При равномерном выборе из 3 файлов каждый выпадает ~100 раз из 300
    assert min(counts.values()) > 50
    pauses = rec.pauses
    assert len(pauses) >= 299
    assert all(1 <= p <= 10 for p in pauses)
    assert len(set(pauses)) > len(pauses) * 0.9
    assert max(pauses) - min(pauses) > 5


def test_first_sound_plays_immediately_and_alternates_with_pauses(tmp_path, sounds_dir):
    rec = Recorder()

    async def scenario():
        manager = make_manager(sounds_dir, rec)
        manager.start(GUILD, FakeVoiceClient(), make_reloader(tmp_path))
        await wait_until(lambda: len(rec.events) >= 10)
        await manager.stop(GUILD.id)

    asyncio.run(scenario())
    kinds = [kind for kind, _ in rec.events[:10]]
    assert kinds == ["play", "sleep"] * 5


def test_stop_ends_endless_session(tmp_path, sounds_dir):
    rec = Recorder()
    vc = FakeVoiceClient()

    async def scenario():
        manager = make_manager(sounds_dir, rec)
        manager.start(GUILD, vc, make_reloader(tmp_path))
        await wait_until(lambda: len(rec.played) >= 20)
        assert manager.is_running(GUILD.id)
        vc.playing = True
        assert await manager.stop(GUILD.id) is True
        assert not manager.is_running(GUILD.id)
        count = len(rec.events)
        for _ in range(100):
            await asyncio.sleep(0)
        assert len(rec.events) == count, "после остановки сессия продолжила играть"
        assert await manager.stop(GUILD.id) is False

    asyncio.run(scenario())
    assert vc.stopped == 1


def test_stop_command(tmp_path, sounds_dir):
    """/stop останавливает идущую сессию, а без сессии отвечает ephemeral-ошибкой."""
    from bot.client import SoundBot

    rec = Recorder()

    def make_interaction():
        interaction = MagicMock()
        interaction.guild = GUILD
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()
        return interaction

    async def scenario():
        bot = SoundBot(guild_id=None)
        bot.sessions = make_manager(sounds_dir, rec)
        stop = bot.tree.get_command("stop").callback

        bot.sessions.start(GUILD, FakeVoiceClient(), make_reloader(tmp_path))
        await wait_until(lambda: len(rec.played) >= 5)
        first = make_interaction()
        await stop(first)
        assert not bot.sessions.is_running(GUILD.id)
        first.response.send_message.assert_awaited_once_with("Остановлено.")

        second = make_interaction()
        await stop(second)
        second.response.send_message.assert_awaited_once_with("Сейчас ничего не играет.", ephemeral=True)
        await bot.close()

    asyncio.run(scenario())


def test_ends_after_failures_in_row(tmp_path, sounds_dir):
    rec = Recorder(fail=lambda path: True)

    async def scenario():
        manager = make_manager(sounds_dir, rec)
        task = manager.start(GUILD, FakeVoiceClient(), make_reloader(tmp_path))
        await asyncio.wait_for(task, timeout=5)
        assert not manager.is_running(GUILD.id)

    asyncio.run(scenario())
    assert len(rec.played) == MAX_FAILURES_IN_ROW
    assert rec.pauses == []


def test_failures_counter_resets_after_success(tmp_path, sounds_dir):
    # Падает всё, кроме a.ogg: подряд 5 ошибок почти наверняка случатся,
    # но только если между ними не было успешного звука.
    # window = 1 снимает ограничение повторов: с ним при 3 звуках 5 «плохих» подряд невозможны
    rec = Recorder(fail=lambda path: path.name != "a.ogg")

    async def scenario():
        manager = make_manager(sounds_dir, rec)
        reloader = make_reloader(tmp_path, extra="[selection]\nwindow = 1\n")
        task = manager.start(GUILD, FakeVoiceClient(), reloader)
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())
    tail = rec.played[-MAX_FAILURES_IN_ROW:]
    assert "a.ogg" not in tail
    streak = 0
    for name in rec.played[:-MAX_FAILURES_IN_ROW]:
        streak = 0 if name == "a.ogg" else streak + 1
        assert streak < MAX_FAILURES_IN_ROW


def test_config_reloaded_before_each_sound(tmp_path, sounds_dir):
    rec = Recorder()
    reloader = make_reloader(tmp_path, 1, 2)

    async def scenario():
        manager = make_manager(sounds_dir, rec)
        manager.start(GUILD, FakeVoiceClient(), reloader)
        await wait_until(lambda: len(rec.pauses) >= 3)
        reloader.path.write_text("[playback]\npause_min = 50\npause_max = 50\n", encoding="utf-8")
        before = len(rec.pauses)
        await wait_until(lambda: len(rec.pauses) >= before + 3)
        await manager.stop(GUILD.id)

    asyncio.run(scenario())
    assert all(1 <= p <= 2 for p in rec.pauses[:3])
    assert rec.pauses[-1] == 50


def test_new_sounds_picked_up_during_session(tmp_path, sounds_dir):
    rec = Recorder()

    async def scenario():
        manager = make_manager(sounds_dir, rec)
        manager.start(GUILD, FakeVoiceClient(), make_reloader(tmp_path))
        await wait_until(lambda: len(rec.played) >= 3)
        (sounds_dir / "new.opus").write_bytes(b"x")
        await wait_until(lambda: "new.opus" in rec.played)
        await manager.stop(GUILD.id)

    asyncio.run(scenario())


def test_ends_when_sounds_dir_emptied(tmp_path, sounds_dir):
    rec = Recorder()

    async def scenario():
        manager = make_manager(sounds_dir, rec)
        task = manager.start(GUILD, FakeVoiceClient(), make_reloader(tmp_path))
        await wait_until(lambda: len(rec.played) >= 3)
        for f in sounds_dir.iterdir():
            f.unlink()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())


def test_ends_when_disconnected(tmp_path, sounds_dir):
    rec = Recorder()
    vc = FakeVoiceClient()

    async def scenario():
        manager = make_manager(sounds_dir, rec)
        task = manager.start(GUILD, vc, make_reloader(tmp_path))
        await wait_until(lambda: len(rec.played) >= 3)
        vc.connected = False
        await asyncio.wait_for(task, timeout=5)
        assert not manager.is_running(GUILD.id)

    asyncio.run(scenario())


def test_selector_state_survives_between_sessions(tmp_path, sounds_dir):
    # 2 звука, after_play = 0: только что сыгравший звук имеет вес 0, поэтому звуки
    # чередуются — в том числе на стыке сессий, если состояние сохранилось.
    # С новым селектором на каждом из 5 стыков повтор был бы с вероятностью 1/2.
    (sounds_dir / "c.wav").unlink()
    extra = "[selection]\nafter_play = 0\n"
    sessions = [Recorder() for _ in range(6)]

    async def scenario():
        manager = make_manager(sounds_dir, sessions[0], seed=1)
        selector = manager.selector(GUILD.id)
        for rec in sessions:
            manager._player, manager._sleep = rec.player, rec.sleep
            manager.start(GUILD, FakeVoiceClient(), make_reloader(tmp_path, extra=extra))
            await wait_until(lambda: len(rec.played) >= 3)
            await manager.stop(GUILD.id)
            assert manager.selector(GUILD.id) is selector

    asyncio.run(scenario())
    combined = [name for rec in sessions for name in rec.played]
    assert all(x != y for x, y in zip(combined, combined[1:])), combined


def test_selectors_are_per_guild(sounds_dir):
    manager = SessionManager(sounds_dir=sounds_dir)
    assert manager.selector(1) is manager.selector(1)
    assert manager.selector(1) is not manager.selector(2)


def test_play_log_contains_chance_and_window(tmp_path, sounds_dir, caplog):
    rec = Recorder()

    async def scenario():
        manager = make_manager(sounds_dir, rec, seed=3)
        manager.start(GUILD, FakeVoiceClient(), make_reloader(tmp_path))
        await wait_until(lambda: len(rec.played) >= 3)
        await manager.stop(GUILD.id)

    with caplog.at_level(logging.DEBUG, logger="bot.session"):
        asyncio.run(scenario())
    messages = [r.getMessage() for r in caplog.records]
    assert any("[1] играю" in m and "(шанс 33%, звуков в папке: 3)" in m for m in messages), messages
    # 3 звука: W = (3 − 2) × 2 + 1 = 3 < window 10; пишется один раз, а не перед каждым звуком
    assert sum("окно повторов сужено с 10 до 3" in m for m in messages) == 1
    assert any("разрешён" in m and "a.ogg" in m for m in messages), "нет отладочной таблицы"


def test_unknown_weight_warned_once(tmp_path, sounds_dir, caplog):
    rec = Recorder()
    extra = '[weights]\n"missing.ogg" = 2\n"A.OGG" = 0.5\n'

    async def scenario():
        manager = make_manager(sounds_dir, rec, seed=4)
        manager.start(GUILD, FakeVoiceClient(), make_reloader(tmp_path, extra=extra))
        await wait_until(lambda: len(rec.played) >= 10)
        await manager.stop(GUILD.id)

    with caplog.at_level(logging.WARNING, logger="bot.session"):
        asyncio.run(scenario())
    warnings = [r.getMessage() for r in caplog.records if "[weights]" in r.getMessage()]
    assert len(warnings) == 1
    # A.OGG совпадает с a.ogg без учёта регистра
    assert "missing.ogg" in warnings[0] and "A.OGG" not in warnings[0]


def test_ends_when_all_weights_zero(tmp_path, sounds_dir):
    rec = Recorder()
    extra = '[weights]\n"a.ogg" = 0\n"b.mp3" = 0\n"c.wav" = 0\n'

    async def scenario():
        manager = make_manager(sounds_dir, rec)
        task = manager.start(GUILD, FakeVoiceClient(), make_reloader(tmp_path, extra=extra))
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())
    assert rec.played == []


def test_short_and_long_sounds_played(tmp_path):
    sounds = tmp_path / "sounds"
    short_dir = sounds / "short"
    long_dir = sounds / "long"
    short_dir.mkdir(parents=True)
    long_dir.mkdir(parents=True)
    (short_dir / "s1.ogg").write_bytes(b"1")
    (short_dir / "s2.ogg").write_bytes(b"2")
    (long_dir / "l1.ogg").write_bytes(b"3")
    (long_dir / "l2.ogg").write_bytes(b"4")

    rec = Recorder()

    async def scenario():
        manager = make_manager(sounds, rec, seed=42)
        manager.start(GUILD, FakeVoiceClient(), make_reloader(tmp_path))
        await wait_until(lambda: len(rec.played) >= 12)
        await manager.stop(GUILD.id)

    asyncio.run(scenario())

    played_names = set(rec.played)
    assert any(name.startswith("s") for name in played_names), played_names
    assert any(name.startswith("l") for name in played_names), played_names

    # Проверяем, что селекторы для short и long независимы
    manager = make_manager(sounds, rec)
    short_sel = manager.selector(GUILD.id, "short")
    long_sel = manager.selector(GUILD.id, "long")
    assert short_sel is not long_sel

