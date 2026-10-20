"""Keys from YAML dictionaries and Jinja2 rendering (datapack, exploit_runner, evalfile)."""
import subprocess
import sys

import pytest

from testsuit.datatools.datapack.templating import Keys, TemplateError, expand_dotted_keys


def _keys(*mappings):
    keys = Keys()
    for i, mapping in enumerate(mappings):
        keys.add(mapping, origin=f"d{i}")
    return keys


def test_merge_and_override():
    keys = _keys({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}, "d": 4})
    assert keys.resolve_all() == {"a": {"b": 1, "c": 3}, "d": 4}
    assert keys.origins == {"a.b": "d0", "a.c": "d1", "d": "d1"}


def test_references_and_native_types():
    keys = _keys({"n": "{{ 2 + 1 }}", "s": "x{{ n }}", "lst": "{{ [n, later] }}", "m": "{{ {'k': n} }}"}, {"later": True})
    assert keys.resolve_all() == {"n": 3, "s": "x3", "lst": [3, True], "m": {"k": 3}, "later": True}


def test_dict_method_names_are_keys():
    keys = _keys({"a": {"values": 1, "items": 2, "keys": 3}})
    assert keys.render_string("{{ a.values }}{{ a.items }}{{ a.keys }}") == "123"


def test_dynamic_key_names():
    keys = _keys({"sensor": {"s1": {"unit": "bar"}}, "id": "s1"})
    assert keys.render_string("{{ sensor[id].unit }}") == "bar"


def test_undefined_and_cycles():
    keys = _keys({"a": "{{ b }}", "b": "{{ a }}", "c": "{{ nope }}"})
    with pytest.raises(TemplateError, match="undefined"):
        keys.render_string("{{ nope }}")
    with pytest.raises(TemplateError) as e:
        keys.resolve_all()
    assert "circular reference: a -> b -> a" in str(e.value)
    assert "'nope' is undefined" in str(e.value)


def test_lazy_resolution():
    # an unresolvable key does not prevent rendering the others
    keys = _keys({"good": 1, "bad": "{{ nope }}"})
    assert keys.render_string("{{ good }}") == 1


def test_expand_dotted_keys():
    assert expand_dotted_keys({"a.b": 1, "a.c": {"d.e": 2}}) == {"a": {"b": 1, "c": {"d": {"e": 2}}}}
    with pytest.raises(TemplateError):
        expand_dotted_keys({"a": 1, "a.b": 2})


def test_render_file_include_and_used_keys(tmp_path):
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc" / "header.txt").write_text("head {{ a.b }}\n")
    (tmp_path / "t.txt").write_text('{% include "inc/header.txt" %}{{ c }} {{ extra }}\n')
    keys = _keys({"a": {"b": 1, "unused": 2}, "c": "{{ a.b + 1 }}"})
    text, used = keys.render_file(tmp_path / "t.txt", extra={"extra": "E"})
    assert text == "head 1\n2 E\n"
    assert used == {"a.b": "d0", "c": "d0"}


def test_render_tree(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "empty").mkdir()
    (src / "sub" / "f.yml").write_text("v: {{ v }}\n")
    (src / "bin.dat").write_bytes(b"\xff\xfe{{")
    used = _keys({"v": 7}).render_tree(src, tmp_path / "dst")
    assert (tmp_path / "dst" / "sub" / "f.yml").read_text() == "v: 7\n"
    assert (tmp_path / "dst" / "bin.dat").read_bytes() == b"\xff\xfe{{"
    assert (tmp_path / "dst" / "empty").is_dir()
    assert used == {"sub/f.yml": {"v": "d0"}}


def test_add_file_errors(tmp_path):
    (tmp_path / "list.yml").write_text("- a\n")
    with pytest.raises(TemplateError, match="mapping"):
        Keys().add_file(tmp_path / "list.yml")
    with pytest.raises(TemplateError, match="cannot read"):
        Keys().add_file(tmp_path / "nope.yml")


def test_evalfile_cli(tmp_path):
    (tmp_path / "d.yml").write_text("a:\n  b: 1\n")
    (tmp_path / "t.txt").write_text("{{ a.b }} {{ x.y }}\n")
    result = subprocess.run([sys.executable, "-m", "testsuit.cli.evalfile", str(tmp_path / "t.txt"), str(tmp_path / "d.yml"),
                             "--set", "x.y=2"], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout == "1 2\n"
    result = subprocess.run([sys.executable, "-m", "testsuit.cli.evalfile", str(tmp_path / "t.txt")], capture_output=True, text=True)
    assert result.returncode == 1 and "undefined" in result.stderr
