from bot.sounds import list_sounds


def touch(directory, *names):
    for name in names:
        (directory / name).write_bytes(b"x")


def test_extensions_case_insensitive(tmp_path):
    touch(tmp_path, "a.MP3", "b.Ogg", "c.OPUS", "d.wav", "e.mp3")
    names = {p.name for p in list_sounds(tmp_path)}
    assert names == {"a.MP3", "b.Ogg", "c.OPUS", "d.wav", "e.mp3"}


def test_unsupported_files_ignored(tmp_path):
    touch(tmp_path, "ok.ogg", "song.m4a", "track.flac", "notes.txt", "noext", ".gitkeep", "fake.mp3.txt")
    sub = tmp_path / "nested"
    sub.mkdir()
    touch(sub, "deep.ogg")
    (tmp_path / "folder.ogg").mkdir()
    assert [p.name for p in list_sounds(tmp_path)] == ["ok.ogg"]


def test_empty_dir(tmp_path):
    assert list_sounds(tmp_path) == []


def test_only_gitkeep(tmp_path):
    touch(tmp_path, ".gitkeep")
    assert list_sounds(tmp_path) == []


def test_missing_dir(tmp_path):
    assert list_sounds(tmp_path / "nope") == []
