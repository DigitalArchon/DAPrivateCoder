"""Every write of a file that matters goes through one helper (config.write_private; the cross-app standard,
Nov 2026): a file of its own in the same folder, 0600 from the start, flushed to the disk, put in place in one
step, and the folder flushed too. Before, five near-copies did it five ways: one with a fixed temporary name
(the commands), one never flushing and leaving its temporary file behind on a failure (the preferences), none
flushing the folder, and the session's files written in place, half a file after a crash."""

import json
import os
import stat
from pathlib import Path

import pytest

from daprivatecoder import commands, config, editor, folders, launcher, manager


@pytest.fixture
def synced(monkeypatch):
    """What os.fsync was asked to flush: a file's descriptor or a folder's, told apart."""
    seen: list[str] = []
    real = os.fsync

    def fsync(fd):
        seen.append("folder" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    return seen


def test_a_file_is_written_whole_private_and_flushed_with_its_folder(tmp_path, synced):
    tmp_path = tmp_path / "w"
    tmp_path.mkdir()
    target = tmp_path / "x.json"
    config.write_private(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}' and stat.S_IMODE(target.stat().st_mode) == 0o600
    assert synced == ["file", "folder"]
    config.write_private(target, b"two", mode=0o755)
    assert target.read_bytes() == b"two" and stat.S_IMODE(target.stat().st_mode) == 0o755
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.json"]        # no temporary file left


def test_a_write_that_fails_leaves_the_file_as_it_was_and_nothing_beside_it(tmp_path, monkeypatch):
    tmp_path = tmp_path / "w"
    tmp_path.mkdir()
    target = tmp_path / "x.json"
    target.write_text("before")

    def no_room(*_a):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "replace", no_room)
    with pytest.raises(OSError):
        config.write_private(target, "after")
    assert target.read_text() == "before" and [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_two_writes_never_share_a_temporary_file(tmp_path, monkeypatch):
    names = []
    real = config.tempfile.mkstemp

    def mkstemp(**kw):
        fd, name = real(**kw)
        names.append(name)
        return fd, name
    monkeypatch.setattr(config.tempfile, "mkstemp", mkstemp)
    config.write_private(tmp_path / "a", "1")
    config.write_private(tmp_path / "a", "2")
    assert len(set(names)) == 2


def test_every_file_that_matters_is_written_through_it(tmp_path, monkeypatch):
    written: list[Path] = []
    real = config.write_private

    def recorded(path, data, mode=0o600):
        written.append(Path(path))
        real(path, data, mode)
    monkeypatch.setattr(config, "write_private", recorded)

    path = config.config_path()
    cfg = config.load()                                                      # the defaults, written once
    config.save(cfg)
    folders.save(folders.Folders({}, "private"))
    manager.Manager()._save_prefs({"theme": "dark"})
    settings = tmp_path / "VSCodium" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{\n    "editor.fontSize": 14\n}\n')
    os.chmod(settings, 0o644)
    assert editor.switch(settings, "/x/claude-editor")
    monkeypatch.setattr(commands, "wrapper_dir", lambda: tmp_path / "bin")
    script = commands.write_editor_script()
    session = launcher.write_settings(["--settings", '{"env": {}}'], os.getpid())
    names = [p.name for p in written]
    assert names.count(path.name) == 2 and folders.path().name in names and manager.prefs_path().name in names
    assert settings.name in names and settings.name + ".daprivatecoder-backup" in names
    assert script.name in names and Path(session[session.index("--settings") + 1]).name in names
    assert stat.S_IMODE(script.stat().st_mode) == 0o755                     # still runnable
    assert stat.S_IMODE(settings.stat().st_mode) == 0o644                   # an editor's settings keep their mode
    assert json.loads(settings.read_text())["editor.fontSize"] == 14
