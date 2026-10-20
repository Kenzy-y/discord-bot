import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "simulate_selection.py"


def run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, encoding="utf-8", timeout=60
    )


def test_simulator_output(tmp_path):
    sounds = tmp_path / "sounds"
    sounds.mkdir()
    for name in ("a.ogg", "b.mp3", "c.wav", "d.opus"):
        (sounds / name).write_bytes(b"x")
    config = tmp_path / "config.toml"
    config.write_text(
        '[playback]\npause_min = 1\npause_max = 2\n[weights]\n"d.opus" = 0\n"ghost.ogg" = 2\n', encoding="utf-8"
    )
    result = run("--sounds", str(sounds), "--config", str(config), "-n", "500", "--seed", "1")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "Итераций: 500" in out
    assert "участвуют в выборе (вес > 0): 3" in out
    assert "ghost.ogg" in out
    assert "Повтор того же звука сразу подряд" in out
    assert "Эффективное окно W: 3 (сужено с 10" in out
    assert "в норме" in out


def test_simulator_reports_config_error(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[playback]\npause_min = 1\n", encoding="utf-8")
    result = run("--config", str(config))
    assert result.returncode == 1
    assert "Ошибка в конфиге" in result.stderr
