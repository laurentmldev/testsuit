"""Extension points used by external libraries: data file formats, mexploit criteria, report logo,
datapack importers, plugins."""
import io,os,shutil,logging
from pathlib import Path

import pytest

from testsuit.datatools.DataFileMgrs import formats
from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgr,GetCsvFileType
from testsuit.datatools.DataFileMgrs.FolderParamMgr import FolderParamMgr
from testsuit.datatools.datatoolbox import SUPPORTED_DATAFILE_EXTENSIONS,loadDataframeFromFile
from testsuit.exploit.mexploit import registry
from testsuit.exploit.mexploit.mexploit import mexploit
from testsuit.exploit.runner import report_html
from testsuit.datatools.datapack import datapack_tools,importers
from testsuit.datatools.datapack.ADataImporter import ADataImporter
from testsuit.datatools.datapack.GitImporter import GitImporter
from testsuit import plugins

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger()

SAMPLE_CSV="tests/etc/data/csv/demo_exploit.clean_02.csv"
TMP_ROOT=Path("tests/tmp/extensions")


@pytest.fixture
def tmp_folder(request):
    folder=TMP_ROOT / request.node.name
    shutil.rmtree(folder,ignore_errors=True)
    folder.mkdir(parents=True)
    return folder


@pytest.fixture(autouse=True)
def no_installed_plugins(monkeypatch):
    """Ignore the plugins installed in the environment (examples/acme_testbench for instance),
    and whatever report logo one of them set before."""
    monkeypatch.setattr(plugins,"entry_points",lambda group: [])
    report_html.set_default_report_logo()


class MyCsvVariantFileMgr(CsvFileMgr):
    """A CSV syntax with a '#MYLOGGER' line on top of the usual header."""
    def getCsvFileType(self) -> str:
        return "mylogger"


################### data file formats ###################

def test_builtin_formats_unchanged():
    for ext in ["h5","hdf5","dxd","d7d","csv","txt","log","tdms","mdf","mf4","influxdbV3.yml","influxdbV2.yml","dat"]:
        assert ext in SUPPORTED_DATAFILE_EXTENSIONS
    assert formats.find_file_format("a/b.CSV").name=="csv"
    assert formats.find_file_format("a/b.h5").name=="hdf5"
    assert formats.find_file_format("a/b.influxdbv3.yml").name=="influxdbV3"
    assert formats.find_file_format("a/b_1_2024-01-02_03-04-05_123456.dat").name=="gantner-udbf"
    # a .dat file not named like Gantner ones is not handled
    assert formats.find_file_format("a/b.dat") is None
    assert GetCsvFileType("tests/etc/data/csv/influxdb.csv")=="influxdb"
    assert GetCsvFileType("tests/etc/data/csv/channels_recording.csv")=="channels"
    assert GetCsvFileType(SAMPLE_CSV)=="Generic"


def test_register_new_format(tmp_folder):
    """A brand new extension, loaded by folder scan and loadDataframeFromFile."""
    shutil.copy(SAMPLE_CSV,tmp_folder / "run1.mydata")
    formats.register_file_format("mydata",[".mydata"],CsvFileMgr)
    try:
        assert "mydata" in SUPPORTED_DATAFILE_EXTENSIONS
        scanner=FolderParamMgr(str(tmp_folder))
        assert [type(m) for m in scanner.getFileMgrs()]==[CsvFileMgr]

        params=loadDataframeFromFile(str(tmp_folder),"Param_1")
        assert len(params)==1 and len(params[0])>0
    finally:
        formats.unregister_file_format("mydata")
    with pytest.raises(Exception, match="unhandle file type"):
        formats.create_file_mgr(str(tmp_folder / "run1.mydata"))


def test_register_csv_variant(tmp_folder):
    """A complementary CSV syntax, detected from the file content."""
    variantFile=tmp_folder / "variant.csv"
    variantFile.write_text("#MYLOGGER v1\n"+Path(SAMPLE_CSV).read_text())
    shutil.copy(SAMPLE_CSV,tmp_folder / "generic.csv")

    formats.register_csv_variant("mylogger",
                                 lambda f: open(f).readline().startswith("#MYLOGGER"),
                                 MyCsvVariantFileMgr)
    try:
        assert GetCsvFileType(str(variantFile))=="mylogger"
        mgrs={os.path.basename(m.getFileName()):type(m) for m in FolderParamMgr(str(tmp_folder)).getFileMgrs()}
        assert mgrs=={"variant.csv":MyCsvVariantFileMgr,"generic.csv":CsvFileMgr}
    finally:
        formats.unregister_csv_variant("mylogger")
    assert GetCsvFileType(str(variantFile))=="Generic"


def test_external_format_overrides_builtin(tmp_folder):
    shutil.copy(SAMPLE_CSV,tmp_folder / "special_run.csv")
    formats.register_file_format("special-csv",["csv"],MyCsvVariantFileMgr,
                                 accepts=lambda f: os.path.basename(f).startswith("special_"))
    try:
        assert type(formats.create_file_mgr(str(tmp_folder / "special_run.csv"))) is MyCsvVariantFileMgr
        assert type(formats.create_file_mgr(SAMPLE_CSV)) is CsvFileMgr
    finally:
        formats.unregister_file_format("special-csv")


################### mexploit criteria ###################

def test_register_criterion_decorator():
    @registry.register_criterion
    def crit_my_decorated(critConf):
        pass

    @registry.register_criterion("renamed")
    def whatever(critConf):
        pass
    try:
        assert registry.get_criterion("my_decorated") is crit_my_decorated
        assert registry.get_criterion("crit_renamed") is whatever
        assert {"my_decorated","renamed"} <= set(registry.get_criteria())
    finally:
        registry.unregister_criterion("my_decorated")
        registry.unregister_criterion("renamed")
    assert registry.get_criterion("my_decorated") is None


def test_register_criteria_module():
    mdl=registry.register_criteria_module("testsuit.exploit.mexploit.criteria")
    try:
        assert registry.get_criterion("assert_compare") is mdl.crit_assert_compare
    finally:
        registry.unregister_criteria_module(mdl)


def test_registered_criterion_in_mexploit_run():
    calls=[]

    def crit_registered_check(critConf):
        calls.append(critConf["value"])
        assert critConf["value"] < critConf["maxValue"]

    registry.register_criterion(crit_registered_check)
    try:
        rst=mexploit("tests/etc/mexploit/scenarii/mxp_OK_registered_crit","tests/etc/data",
                     "tests/tmp/mexploit/mxp_OK_registered_crit",force=True)
    finally:
        registry.unregister_criterion("registered_check")
    assert rst==0
    assert calls==[3]


################### HTML report logo ###################

def _minimal_exploit_info(results_path):
    return {"name":"logo test","results_path":results_path,"sessions":{},
            "nbSuccess":0,"nbAccepted":0,"nbRejected":0,"nbFailed":0,"nbError":0}


def test_default_logo():
    svg=report_html.make_report_logo_svg()
    assert svg.strip().startswith("<svg") and "M-Exploit" in svg and "@keyframes" in svg
    assert report_html.SVG_LOGO==svg


def test_logo_title_is_escaped():
    svg=report_html.make_report_logo_svg('A&B "<lab>"')
    assert "A&amp;B &quot;&lt;lab&gt;&quot;" in svg
    assert "<lab>" not in svg


@pytest.mark.parametrize("kind",["default","title","markup","file","none"])
def test_report_logo(tmp_folder,kind):
    customSvg='<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" id="acme-logo"><text>ACME</text></svg>'
    svgFile=tmp_folder / "logo.svg"
    svgFile.write_text(customSvg)
    kwargs={"default":{},
            "title":{"logo_title":"ACME Bench"},
            "markup":{"logo_svg":customSvg},
            "file":{"logo_svg":svgFile},
            "none":{"logo_svg":""}}[kind]

    html=report_html.generate_report_html(_minimal_exploit_info(tmp_folder),**kwargs).read_text()

    assert ("M-Exploit</text>" in html)==(kind=="default")
    assert ("ACME Bench</text>" in html)==(kind=="title")
    assert ('id="acme-logo"' in html)==(kind in ("markup","file"))
    assert "<?xml" not in html
    assert ("<svg" in html)==(kind!="none")


def test_set_default_report_logo(tmp_folder):
    report_html.set_default_report_logo(title="ACME Bench")
    try:
        html=report_html.generate_report_html(_minimal_exploit_info(tmp_folder)).read_text()
        assert "ACME Bench</text>" in html
        # an explicit logo still wins
        html=report_html.generate_report_html(_minimal_exploit_info(tmp_folder),logo_title="Other").read_text()
        assert "Other</text>" in html
    finally:
        report_html.set_default_report_logo()
    assert "M-Exploit</text>" in report_html.generate_report_html(_minimal_exploit_info(tmp_folder)).read_text()


def test_invalid_logo():
    with pytest.raises(ValueError):
        report_html.load_logo_svg("<div>not a logo</div>")


################### datapack importers ###################

class FakeImporter(ADataImporter):
    """Imports a dataset by writing its dataset.dico, the version being in a VERSION file."""
    retrieved=[]

    def retrieve(self):
        os.makedirs(self._targetDir,exist_ok=True)
        Path(self._targetDir,"VERSION").write_text(self._versionId)
        Path(self._targetDir,"dataset.dico").write_text(f"fake.remote={self._remotePath}\n")
        FakeImporter.retrieved.append(self._versionId)
        return True

    def getTag(self,testTag=False):
        return Path(self._targetDir,"VERSION").read_text()

    def getChanges(self,testClean=False):
        return True if testClean else []

    def checkVersion(self,expectedVersion):
        return self.getTag()==expectedVersion


def test_builtin_git_importer():
    assert "git" in importers.get_data_importers()
    importer=importers.create_data_importer("Git","target","https://example.com/repo.git","v1")
    assert type(importer) is GitImporter
    with pytest.raises(KeyError, match="unknown datasource importer 'nope'"):
        importers.create_data_importer("nope","target","remote","v1")


def test_register_data_importer(tmp_folder):
    FakeImporter.retrieved.clear()
    importers.register_data_importer("fake",FakeImporter)
    try:
        dataset={"id":"my_dataset","path":"v1"}
        dicos=datapack_tools.loadDataset(str(tmp_folder),"FAKE","my/remote",dataset,io.StringIO())
        assert dicos==[str(tmp_folder / "my_dataset" / "v1" / "dataset.dico")]
        assert Path(dicos[0]).read_text()=="fake.remote=my/remote\n"
        # already imported: only its version is checked
        datapack_tools.loadDataset(str(tmp_folder),"fake","my/remote",dataset,io.StringIO())
        assert FakeImporter.retrieved==["v1"]
    finally:
        importers.unregister_data_importer("fake")
    with pytest.raises(SystemExit):
        datapack_tools.loadDataset(str(tmp_folder),"fake","my/remote",{"id":"other","path":"v1"},io.StringIO())


################### plugins ###################

def test_plugins_from_env(tmp_folder,monkeypatch):
    (tmp_folder / "my_testsuit_plugin.py").write_text("""
from testsuit.datatools.DataFileMgrs.formats import register_file_format
from testsuit.datatools.DataFileMgrs.CsvFileMgr import CsvFileMgr
from testsuit.exploit.mexploit.registry import register_criterion

def register():
    register_file_format("plugin-format", ["plugdata"], CsvFileMgr)
    register_criterion("plugin_check", lambda critConf: None)
""")
    monkeypatch.syspath_prepend(str(tmp_folder))
    monkeypatch.setenv(plugins.ENV_VAR,"my_testsuit_plugin, missing_testsuit_plugin")
    monkeypatch.setattr(plugins,"_loaded",set())
    try:
        # a plugin failing to load does not stop the others
        assert plugins.load_plugins()==["my_testsuit_plugin"]
        assert formats.find_file_format("x.plugdata").name=="plugin-format"
        assert registry.get_criterion("plugin_check") is not None
        # loaded once
        assert plugins.load_plugins()==[]
    finally:
        formats.unregister_file_format("plugin-format")
        registry.unregister_criterion("plugin_check")


def test_plugin_criterion_in_mxp_process(tmp_folder):
    """exploit_runner runs 'mxp' as a separate process: a plugin is how its criteria get there."""
    import subprocess,sys
    (tmp_folder / "my_mxp_plugin.py").write_text("""
from testsuit.exploit.mexploit.registry import register_criterion

@register_criterion
def crit_registered_check(critConf):
    assert critConf["value"] < critConf["maxValue"]
""")
    env=dict(os.environ)
    env["PYTHONPATH"]=os.pathsep.join([str(tmp_folder.resolve()),str(Path("src").resolve()),env.get("PYTHONPATH","")])
    env[plugins.ENV_VAR]="my_mxp_plugin"
    proc=subprocess.run([sys.executable,"-c","from testsuit.cli.mxp import main; main()",
                         "tests/etc/mexploit/scenarii/mxp_OK_registered_crit","tests/etc/data",
                         "-o",str(tmp_folder / "results"),"-f"],
                        env=env,capture_output=True,text=True,timeout=300)
    log.info(proc.stdout+proc.stderr)
    assert proc.returncode==0
