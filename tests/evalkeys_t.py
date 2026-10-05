from pathlib import Path

from testsuit.datatools.datapack.evalfiles import evalfile, evalkeys, libdictionary


def _replace(dico, text, ignoreMissing=False):
    evalkeys.resetCounters()
    evalkeys.setDico(dico)
    lines, used, undefined = evalkeys.replaceKeys("test.txt", text.split("\n"), ignoreMissing)
    return "\n".join(evalkeys.finalizeLine(line) for line in lines), used, undefined


def test_value_referencing_other_keys():
    text, used, _ = _replace({"a": "_K_(b)", "b": "x _K_(c) y", "c": "C"}, "<_K_(a)>")
    assert text == "<x C y>"
    assert set(used) == {"a", "b", "c"}


def test_key_name_built_from_other_keys():
    dico = {"id": "42", "which": "id", "sensor.42.unit": "bar"}
    text, _, _ = _replace(dico, "_K_(sensor._K_(id).unit) _K_(sensor._K_(_K_(which)).unit)")
    assert text == "bar bar"


def test_xml_key_ref():
    text, _, _ = _replace({"x": "1", "y": "2"}, "<_key_ src=\"x\"/>, <_KEY_ src='y' />")
    assert text == "1, 2"


def test_undefined_keys():
    text, _, undefined = _replace({}, "_K_(nope) _K_(other)")
    assert text == "_K_?(nope) _K_?(other)"
    assert evalkeys.nbUndefined == 2
    message = evalkeys.getUndefinedKeysStr(undefined)
    assert "'nope'" in message and "'other'" in message

    text, _, _ = _replace({}, "_K_(nope)", ignoreMissing=True)
    assert text == "_K_(nope)"
    assert evalkeys.nbUndefined == 0


def test_circular_reference():
    text, _, _ = _replace({"a": "_K_(b)", "b": "_K_(a)"}, "_K_(a)")
    assert text == "_K_!(a)"
    assert evalkeys.nbInfinateRecursion == 1


def test_environment_keys():
    text, _, _ = _replace({}, "_K_(_ENV_HOME_)")
    assert text == str(Path.home())


def test_set_dico_with_current_dico_keeps_it():
    evalkeys.setDico({"a": "1"})
    evalkeys.setDico(*evalkeys.getDico())
    assert evalkeys.getDico()[0]["a"] == "1"


def test_dico_file_and_overrides(tmp_path):
    (tmp_path / "main.dico").write_text("x=main\nm=line1\n>line2\n# comment\n")
    (tmp_path / "base.dico").write_text("x=base\ny=base\n")
    dico, origins = libdictionary.loadDicos([str(tmp_path / "main.dico"), str(tmp_path / "base.dico")])
    assert dico["x"] == "main" and dico["y"] == "base"
    assert dico["m"] == "line1" + evalkeys.NEW_LINE_MARKER + "line2"
    assert dico["x.overrides.values"] == "base"
    assert origins["x"].endswith("main.dico")


def test_errors_are_counted_per_file(tmp_path):
    (tmp_path / "d.dico").write_text("x=1\n")
    (tmp_path / "bad.txt").write_text("_K_(undefined)\n")
    (tmp_path / "good.txt").write_text("_K_(x)\n")
    dicos = [str(tmp_path / "d.dico")]
    evalfile.evalfile(str(tmp_path / "bad.txt"), dicos)
    assert evalkeys.nbUndefined == 1
    lines, _ = evalfile.evalfile(str(tmp_path / "good.txt"), dicos)
    assert evalkeys.nbUndefined == 0
    assert [evalkeys.finalizeLine(line) for line in lines] == ["1"]



def test_html_view_links_to_key_origin(tmp_path):
    (tmp_path / "d.dico").write_text("x=1\n")
    (tmp_path / "src.txt").write_text("v=_K_(x)\n")
    lines, used = evalfile.evalfile(str(tmp_path / "src.txt"), [str(tmp_path / "d.dico")])
    evalfile.finalizeLines(lines, used, outputFile=str(tmp_path / "out.txt"))
    html = (tmp_path / ".out.txt.html").read_text()
    assert f'<a href="{tmp_path / "d.dico"}#x" title="x" >1</a>' in html
