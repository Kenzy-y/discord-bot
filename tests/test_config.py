import logging

import pytest

from bot.config import ConfigError, ConfigReloader, PlaybackConfig, SelectionConfig, load_config

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


class TestSelectionAndWeights:
    def test_old_config_uses_defaults(self, cfg_path):
        config = load_config(write(cfg_path, VALID))
        assert config.selection == SelectionConfig(
            after_play=0.1, growth=0.3, max_weight=3.0, window=10, max_repeats=2
        )
        assert config.weights.entries == {}
        assert config.weights.weight_for("anything.ogg") == 1.0

    def test_custom_values(self, cfg_path):
        text = VALID + """
[selection]
after_play = 0
growth = 0.5
max_weight = 5
window = 4
max_repeats = 1

[weights]
"Rare.ogg" = 0.3
"off.mp3" = 0
"loud.wav" = 2
"""
        config = load_config(write(cfg_path, text))
        assert config.selection == SelectionConfig(0.0, 0.5, 5.0, 4, 1)
        assert config.weights.weight_for("rare.OGG") == 0.3
        assert config.weights.weight_for("off.mp3") == 0.0
        assert config.weights.weight_for("loud.wav") == 2.0
        assert config.weights.weight_for("other.ogg") == 1.0
        assert config.weights.names() == ["Rare.ogg", "off.mp3", "loud.wav"]

    def test_partial_selection_uses_defaults_for_missing_keys(self, cfg_path):
        config = load_config(write(cfg_path, VALID + "[selection]\nwindow = 3\n"))
        assert config.selection == SelectionConfig(window=3)

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ("after_play = -0.1", "selection.after_play: должно быть не меньше 0"),
            ("growth = -1", "selection.growth: должно быть не меньше 0"),
            ("max_weight = 0.9", "selection.max_weight: должно быть не меньше 1.0"),
            ("after_play = 4\nmax_weight = 3", r"selection.after_play \(4\) больше selection.max_weight"),
            ("window = 0", "selection.window: должно быть не меньше 1"),
            ("window = 2.5", "selection.window: должно быть целым числом"),
            ("max_repeats = 0", "selection.max_repeats: должно быть не меньше 1"),
            ("max_repeats = true", "selection.max_repeats: должно быть целым числом"),
            ("growth = 'fast'", "selection.growth: должно быть числом"),
        ],
    )
    def test_selection_errors(self, cfg_path, body, message):
        write(cfg_path, VALID + f"[selection]\n{body}\n")
        with pytest.raises(ConfigError, match=message):
            load_config(cfg_path)

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ('"a.ogg" = -1', "weights.a.ogg: должно быть не меньше 0"),
            ('"a.ogg" = "много"', "weights.a.ogg: должно быть числом"),
            ("a.ogg = 0.5", 'имя файла нужно писать в кавычках: "a.ogg"'),
            ('"A.ogg" = 1\n"a.OGG" = 2', "один и тот же файл"),
        ],
    )
    def test_weights_errors(self, cfg_path, body, message):
        write(cfg_path, VALID + f"[weights]\n{body}\n")
        with pytest.raises(ConfigError, match=message):
            load_config(cfg_path)

    def test_section_must_be_table(self, cfg_path):
        write(cfg_path, "selection = 1\n" + VALID)
        with pytest.raises(ConfigError, match=r"\[selection\] в config.toml должно быть секцией"):
            load_config(cfg_path)

    def test_reloader_keeps_last_good_selection(self, cfg_path, caplog):
        reloader = ConfigReloader(write(cfg_path, VALID + "[selection]\nwindow = 4\n"))
        good = reloader.load()
        write(cfg_path, VALID + "[selection]\nwindow = 0\n")
        with caplog.at_level(logging.WARNING, logger="bot.config"):
            assert reloader.get() == good
            assert reloader.get() == good
        assert len(caplog.records) == 1
