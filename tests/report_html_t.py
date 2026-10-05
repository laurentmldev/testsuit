"""exploit_runner HTML report: failed assert statements and embedded figures size."""
import base64,gzip,re,shutil,logging
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from testsuit.datatools.plotHelpers import save_svg_figure
from testsuit.exploit.mexploit import registry
from testsuit.exploit.mexploit.mexploit import mexploit
from testsuit.exploit.runner import report_html

logging.basicConfig(level=logging.DEBUG)

TMP_ROOT=Path("tests/tmp/report_html")


@pytest.fixture
def tmp_folder(request):
    folder=TMP_ROOT / request.node.name
    shutil.rmtree(folder,ignore_errors=True)
    folder.mkdir(parents=True)
    return folder


################### failed assert ###################

def test_failed_assert_in_report(tmp_folder):
    def crit_registered_check(critConf):
        assert critConf["value"] \
            < critConf["maxValue"], "value too high"

    registry.register_criterion(crit_registered_check)
    try:
        rst=mexploit("tests/etc/mexploit/scenarii/mxp_NOK_registered_crit","tests/etc/data",str(tmp_folder),force=True)
    finally:
        registry.unregister_criterion("registered_check")
    assert rst!=0

    testcase=ET.parse(tmp_folder / "pytest_report.xml").getroot().find(".//testcase[@name='crit_check01']")
    props={p.get("name"):p.get("value") for p in testcase.iterfind("properties/property")}
    assert props["failed_assert"]=='assert critConf["value"] \\\n            < critConf["maxValue"]'
    assert re.fullmatch(r"report_html_t\.py:\d+",props["failed_assert_location"])

    html=report_html._failure_html(testcase,testcase.find("failure"),"FAILED","status-failed-details")
    assert "AssertionError: value too high" in html
    assert "<b>Failed assert</b>" in html
    assert 'critConf["value"] \\\n            &lt; critConf["maxValue"]' in html


def _failure_node(message, text):
    testcase=ET.fromstring("<testcase name='crit_x'><failure/></testcase>")
    failure=testcase.find("failure")
    failure.set("message",message)
    failure.text=text
    return testcase,failure


def test_failed_assert_from_traceback():
    """Reports of runs made before the failed_assert property existed."""
    testcase,failure=_failure_node("AssertionError: too high",
        "my_lib/checks.py:12: in crit_max\n    assert x < 10, f'too high {x}'\n           ^^^^^^\nE   AssertionError: too high")
    assert report_html._failed_assert(testcase,failure)==("assert x < 10","checks.py:12")


def test_no_failed_assert_for_builtin_criteria_and_exceptions():
    testcase,failure=_failure_node("AssertionError: CRIT_CHECK",
        "src/testsuit/exploit/mexploit/criteria.py:607: in crit_assert_compare\n    assert testOk, 'CRIT_CHECK'\nE   AssertionError: CRIT_CHECK")
    assert report_html._failed_assert(testcase,failure) is None
    testcase,failure=_failure_node("Exception: while evaluating <unknown>",
        "my_lib/checks.py:12: in crit_max\n    assert_ready()\nE   Exception: while evaluating <unknown>")
    assert report_html._failed_assert(testcase,failure) is None
    assert "while evaluating &lt;unknown&gt;" in report_html._failure_html(testcase,failure,"FAILED","status-failed-details")


################### embedded figures ###################

def test_embedded_svg_is_gzipped(tmp_folder):
    svg=b"<svg xmlns='http://www.w3.org/2000/svg'>"+b"<path d='M 0 0 L 10.5 10.5'/>"*1000+b"</svg>"
    (tmp_folder / "fig.svg").write_bytes(svg)
    attrs=report_html._embedded_figure_attrs(tmp_folder / "fig.svg")
    data=re.fullmatch(r'data-svgz="([^"]+)"',attrs).group(1)
    assert gzip.decompress(base64.b64decode(data))==svg
    assert len(data)<len(svg)/10

    png=b"\x89PNG"
    (tmp_folder / "fig.png").write_bytes(png)
    assert report_html._embedded_figure_attrs(tmp_folder / "fig.png")==f'src="data:image/png;base64,{base64.b64encode(png).decode()}"'


def test_svg_figure_texts_are_not_paths(tmp_folder):
    fig,ax=plt.subplots()
    ax.plot([0,1],[0,1])
    ax.set_title("my title")
    save_svg_figure(fig,str(tmp_folder / "fig.svg"))
    plt.close(fig)
    svg=(tmp_folder / "fig.svg").read_text()
    assert ">my title</text>" in svg
    assert "DejaVuSans-" not in svg  # no glyph drawn as path
