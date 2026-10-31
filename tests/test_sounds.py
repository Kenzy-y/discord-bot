from bot.sounds import get_sounds_by_type, list_all_sounds, list_sounds


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


def test_get_sounds_by_type_short_and_long(tmp_path):
    short_dir = tmp_path / "short"
    long_dir = tmp_path / "long"
    short_dir.mkdir()
    long_dir.mkdir()
    touch(short_dir, "s1.ogg", "s2.mp3")
    touch(long_dir, "l1.wav")

    by_type = get_sounds_by_type(tmp_path)
    assert set(by_type.keys()) == {"short", "long"}
    assert [p.name for p in by_type["short"]] == ["s1.ogg", "s2.mp3"]
    assert [p.name for p in by_type["long"]] == ["l1.wav"]
    assert [p.name for p in list_all_sounds(tmp_path)] == ["l1.wav", "s1.ogg", "s2.mp3"]


def test_get_sounds_by_type_only_one_subfolder(tmp_path):
    short_dir = tmp_path / "short"
    long_dir = tmp_path / "long"
    short_dir.mkdir()
    long_dir.mkdir()
    touch(short_dir, "s1.ogg")
    # long is empty

    by_type = get_sounds_by_type(tmp_path)
    assert set(by_type.keys()) == {"short"}
    assert [p.name for p in by_type["short"]] == ["s1.ogg"]
    assert [p.name for p in list_all_sounds(tmp_path)] == ["s1.ogg"]


def test_get_sounds_by_type_flat_fallback(tmp_path):
    touch(tmp_path, "flat1.ogg", "flat2.mp3")
    by_type = get_sounds_by_type(tmp_path)
    assert set(by_type.keys()) == {"short"}
    assert [p.name for p in by_type["short"]] == ["flat1.ogg", "flat2.mp3"]
    assert [p.name for p in list_all_sounds(tmp_path)] == ["flat1.ogg", "flat2.mp3"]


def test_get_sounds_by_type_empty_and_missing(tmp_path):
    assert get_sounds_by_type(tmp_path) == {}
    assert list_all_sounds(tmp_path) == []
    assert get_sounds_by_type(tmp_path / "missing") == {}
    assert list_all_sounds(tmp_path / "missing") == []

