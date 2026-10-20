import logging

import pytest

from bot.config import ConfigError, ConfigReloader, PlaybackConfig, load_config

VALID = """
[playback]
pause_min = 2
pause_max = 7.5
"""


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def cfg_path(tmp_path):
    return tmp_path / "config.toml"


def test_valid(cfg_path):
    config = load_config(write(cfg_path, VALID))
    assert config.playback == PlaybackConfig(pause_min=2.0, pause_max=7.5)


def test_equal_bounds_allowed(cfg_path):
    config = load_config(write(cfg_path, "[playback]\npause_min = 0\npause_max = 0\n"))
    assert config.playback == PlaybackConfig(pause_min=0.0, pause_max=0.0)


def test_min_greater_than_max(cfg_path):
    write(cfg_path, "[playback]\npause_min = 10\npause_max = 5\n")
    with pytest.raises(ConfigError, match="pause_min .* больше .*pause_max"):
        load_config(cfg_path)


def test_negative_values(cfg_path):
    write(cfg_path, "[playback]\npause_min = -1\npause_max = -0.5\n")
    with pytest.raises(ConfigError) as e:
        load_config(cfg_path)
    assert "pause_min: должно быть не меньше 0" in str(e.value)
    assert "pause_max: должно быть не меньше 0" in str(e.value)


@pytest.mark.parametrize(
    ("text", "missing"),
    [
        ("[playback]\npause_max = 5\n", ["pause_min"]),
        ("[playback]\npause_min = 5\n", ["pause_max"]),
        ("[playback]\n", ["pause_min", "pause_max"]),
    ],
)
def test_missing_fields(cfg_path, text, missing):
    write(cfg_path, text)
    with pytest.raises(ConfigError) as e:
        load_config(cfg_path)
    for key in missing:
        assert f"playback.{key}: параметр не задан" in str(e.value)


def test_missing_section(cfg_path):
    write(cfg_path, "[other]\nx = 1\n")
    with pytest.raises(ConfigError, match=r"нет секции \[playback\]"):
        load_config(cfg_path)


@pytest.mark.parametrize("value", ["'5'", "true", "[1, 2]"])
def test_not_a_number(cfg_path, value):
    write(cfg_path, f"[playback]\npause_min = {value}\npause_max = 5\n")
    with pytest.raises(ConfigError, match="pause_min: должно быть числом"):
        load_config(cfg_path)


def test_unknown_fields_ignored(cfg_path):
    # plays_min/plays_max из старых версий конфига не должны ломать загрузку
    write(cfg_path, VALID + "plays_min = 3\nplays_max = 10\nfoo = 'bar'\n\n[future]\nx = 1\n")
    config = load_config(cfg_path)
    assert config.playback == PlaybackConfig(pause_min=2.0, pause_max=7.5)


def test_syntax_error(cfg_path):
    write(cfg_path, "[playback\n")
    with pytest.raises(ConfigError, match="синтаксическая ошибка"):
        load_config(cfg_path)


def test_missing_file(cfg_path):
    with pytest.raises(ConfigError, match="не найден"):
        load_config(cfg_path)


class TestReloader:
    def test_applies_changes(self, cfg_path):
        reloader = ConfigReloader(write(cfg_path, VALID))
        reloader.load()
        write(cfg_path, "[playback]\npause_min = 1\npause_max = 1\n")
        assert reloader.get().playback == PlaybackConfig(1.0, 1.0)

    def test_invalid_keeps_last_good_and_warns_once_per_change(self, cfg_path, caplog):
        reloader = ConfigReloader(write(cfg_path, VALID))
        good = reloader.load()

        write(cfg_path, "[playback]\npause_min = 9\npause_max = 1\n")
        with caplog.at_level(logging.WARNING, logger="bot.config"):
            for _ in range(5):
                assert reloader.get() == good
        assert len(caplog.records) == 1

        # Новое изменение файла, всё ещё некорректное, — ещё одно предупреждение
        caplog.clear()
        write(cfg_path, "[playback\n")
        with caplog.at_level(logging.WARNING, logger="bot.config"):
            for _ in range(3):
                assert reloader.get() == good
        assert len(caplog.records) == 1

        write(cfg_path, "[playback]\npause_min = 3\npause_max = 4\n")
        assert reloader.get().playback == PlaybackConfig(3.0, 4.0)

    def test_deleted_file_keeps_last_good(self, cfg_path, caplog):
        reloader = ConfigReloader(write(cfg_path, VALID))
        good = reloader.load()
        cfg_path.unlink()
        with caplog.at_level(logging.WARNING, logger="bot.config"):
            assert reloader.get() == good
            assert reloader.get() == good
        assert len(caplog.records) == 1

    def test_load_raises_on_invalid(self, cfg_path):
        reloader = ConfigReloader(write(cfg_path, "[playback]\npause_min = 2\n"))
        with pytest.raises(ConfigError):
            reloader.load()
