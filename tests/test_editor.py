"""The editor switch: one entry of a hand-edited settings file changed in place, nothing else."""

import json

import pytest

from daprivatecoder import editor
from daprivatecoder.editor import KEY, EditorError, loads, remove_key, set_value

WRAPPER = "/home/user/.local/bin/claude-private"

SAMPLES = [
    "",
    "{}",
    "{\n}\n",
    '{\n    "editor.fontSize": 14\n}\n',
    '{\n    "editor.fontSize": 14,\n}\n',                          # a trailing comma (VS Code takes it)
    '{\n    // my settings\n    "a": "http://x // not a comment", /* b */ "b": [1, {"c": "}"}],\n'
    '    "d": "say \\"hi\\""  // last\n}\n',
    '{"nested": {"claudeCode.claudeProcessWrapper": "inner"}, "z": null}',
    '{\n  "claudeCode.preferredLocation": "panel",\n  "claudeCode.hideOnboarding": true\n}',
]


@pytest.mark.parametrize("text", SAMPLES)
def test_switching_on_and_off_changes_that_entry_only(text):
    before = loads(text)
    on = set_value(text, KEY, WRAPPER)
    assert loads(on) == {**before, KEY: WRAPPER}
    again = set_value(on, KEY, "/other/claude-private")                 # replaced where it is
    assert loads(again) == {**before, KEY: "/other/claude-private"} and again.count(KEY) == on.count(KEY)
    off = remove_key(on, KEY)
    assert loads(off) == before
    assert remove_key(text, KEY) == text                                 # not there: nothing changes


def test_the_rest_of_the_text_is_kept_as_it_was():
    text = '{\n    // my settings\n    "editor.fontSize": 14, // big\n    "files.autoSave": "off"\n}\n'
    on = set_value(text, KEY, WRAPPER)
    assert on.startswith('{\n    // my settings\n    "editor.fontSize": 14, // big\n    "files.autoSave": "off"')
    assert remove_key(on, KEY) == text
    middle = '{\n    "a": 1,\n    "claudeCode.claudeProcessWrapper": "/x",\n    "b": 2\n}\n'
    assert remove_key(middle, KEY) == '{\n    "a": 1,\n    "b": 2\n}\n'
    assert remove_key('{"a": 1, "claudeCode.claudeProcessWrapper": "/x"}', KEY) == '{"a": 1}'


def test_comments_and_strings_dont_confuse_it():
    data = loads('{"url": "http://a//b", "c": "/* no */", "q": "a\\"b", // x\n "n": 1,}')
    assert data == {"url": "http://a//b", "c": "/* no */", "q": 'a"b', "n": 1}
    with pytest.raises(EditorError):
        loads('{"a": }')
    with pytest.raises(EditorError):
        loads("[1, 2]")


def test_switch_keeps_a_backup_and_refuses_a_file_it_cant_read(tmp_path):
    path = tmp_path / "User" / "settings.json"
    path.parent.mkdir()
    path.write_text('{\n    "editor.fontSize": 14 // mine\n}\n')
    assert editor.switch(path, WRAPPER) is True
    assert json.loads(path.with_name("settings.json.daprivatecoder-backup").read_text().replace("// mine", "")) == \
        {"editor.fontSize": 14}
    assert editor.current(path) == WRAPPER and "// mine" in path.read_text()
    assert editor.switch(path, WRAPPER) is False                        # already so
    assert editor.switch(path, None) is True and editor.current(path) is None
    path.write_text('{"editor.fontSize": 14,, }')
    with pytest.raises(EditorError, match="wasn't changed"):
        editor.switch(path, WRAPPER)
    assert path.read_text() == '{"editor.fontSize": 14,, }'
    fresh = tmp_path / "Fresh" / "settings.json"
    assert editor.switch(fresh, WRAPPER) and editor.current(fresh) == WRAPPER


@pytest.mark.parametrize("text", SAMPLES + ['{\r\n    "editor.fontSize": 14\r\n}\r\n', "\n\n",
                                            '{\n    "a": 1, // one\n}\n'])
def test_off_gives_the_file_back_as_it_was_before_on(tmp_path, text):
    """Taking the entry out left an empty file as "{}", dropped a trailing comma, and a file of CRLF lines, read as
    text, was written back with LF ones (until Nov 2026). Off gives back the file as it was, kept beside it, while the
    file is as on left it."""
    path = tmp_path / "settings.json"
    path.write_bytes(text.encode("utf-8"))
    assert editor.switch(path, WRAPPER) and editor.current(path) == WRAPPER
    on = path.read_bytes()
    if "\r\n" in text:
        assert on.count(b"\n") == on.count(b"\r\n")                       # its own line ends, the new line's too
    assert editor.switch(path, None)
    assert path.read_bytes() == text.encode("utf-8")


def test_off_after_the_file_was_edited_takes_out_the_entry_alone(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{\n    "a": 1,\n}\n')
    editor.switch(path, WRAPPER)
    path.write_text(path.read_text().replace('"a": 1', '"a": 2'))          # changed by hand since
    editor.switch(path, None)
    assert loads(path.read_text()) == {"a": 2} and editor.current(path) is None
