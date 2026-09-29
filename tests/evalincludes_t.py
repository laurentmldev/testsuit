from testsuit.datatools.datapack.evalfiles import evalincludes


def test_include_params_do_not_leak_into_the_next_include(tmp_path):
    main = tmp_path / "main.txt"
    main.write_text('<_include_ src="missing1.txt" >\n'
                    '<param name="a">1</param>\n'
                    '</_include_>\n'
                    '<_include_ src="missing2.txt" >\n'
                    '</_include_>\n')
    lines, has_errors, _ = evalincludes.expandFileIncludes(str(main))
    text = "\n".join(lines)
    assert has_errors  # both included files are missing
    assert text.count('<param name="a">1</param>') == 1
