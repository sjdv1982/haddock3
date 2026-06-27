import pytest
import itertools
import os
from pathlib import Path
import tempfile
import shlex
from unittest.mock import MagicMock
from haddock.libs.libsubprocess import BaseJob, Job, CNSJob
from haddock.libs.libseamless import scan_cns_dependencies


@pytest.fixture
def basejob():
    """Create a BaseJob instance."""
    return BaseJob(
        input_=Path("input"),
        output=Path("output"),
        executable=Path("executable"),
    )


@pytest.fixture
def job():
    """Create a Job instance."""
    return Job(
        input_=Path("input"),
        output=Path("output"),
        executable=Path("executable"),
    )


@pytest.fixture
def cnsjob(mocker):
    """Create a CNSJob instance.

    Here we create a temporary file and set it as the mock CNS executable.
    """
    with tempfile.NamedTemporaryFile() as f:
        f.file.write(b"")
        f.file.flush()
        f.file.seek(0)
        os.chmod(f.name, 0o755)

        mocker.patch("haddock.libs.libsubprocess.global_cns_exec", f.name)

        yield CNSJob(
            input_file=Path("input"),
            output_file=Path("output"),
            cns_exec=Path(f.name),
        )


def test_basejob_run(basejob, mocker):
    """Test the run method of the BaseJob class."""
    basejob.make_cmd = lambda: None
    basejob.cmd = "some_command"

    mock_popen = mocker.patch("subprocess.Popen")
    mock_popen_instance = MagicMock()
    mock_popen.return_value = mock_popen_instance
    mock_popen_instance.communicate.return_value = (b"output", None)

    mock_open = mocker.patch("builtins.open", mocker.mock_open())

    result = basejob.run()

    assert result == b"output"

    basejob.make_cmd()
    assert mock_popen.call_args[0][0] == shlex.split(basejob.cmd)
    mock_open.assert_called_once_with(basejob.output, "w")

    mock_popen.assert_called_once_with(
        shlex.split(basejob.cmd),
        stdout=mock_open(),
        close_fds=True,
    )


def test_basejob_make_cmd(basejob):
    with pytest.raises(NotImplementedError):
        basejob.make_cmd()


def test_jobinputfirst_make_cmd(job):
    job.executable = "executable"
    job.input = "input"
    job.args = [1, 2, 3]

    job.make_cmd()

    assert job.cmd == "executable 123 input"


def test_cnsjob_envvars_setter(cnsjob):
    cnsjob.envvars = {"key": "value"}

    assert cnsjob.envvars == {"key": "value"}
    assert cnsjob._envvars == {"key": "value"}

    with pytest.raises(ValueError):
        cnsjob.envvars = "wrong"


def test_cnsjob_cns_exec_setter(cnsjob, mocker):

    with tempfile.NamedTemporaryFile() as f:
        f.file.write(b"")
        f.file.flush()
        f.file.seek(0)
        os.chmod(f.name, 0o755)

        mocker.patch("haddock.libs.libsubprocess.global_cns_exec", f.name)

        cnsjob.cns_exec = f.name

    with pytest.raises(ValueError):
        cnsjob.cns_exec = "wrong"


def test_cnsjob_run(cnsjob, mocker):

    mock_popen = mocker.patch("subprocess.Popen")
    mock_popen_instance = MagicMock()
    mock_popen.return_value = mock_popen_instance
    mock_popen_instance.communicate.return_value = (b"output", None)

    mocker.patch("builtins.open", mocker.mock_open())
    mocker.patch("haddock.libs.libsubprocess.gzip_files", return_value=None)

    # Try all possible combinations of compress flags
    for comb in itertools.product([True, False], repeat=3):
        compress_inp, compress_out, compress_seed = comb
        result = cnsjob.run(
            compress_inp=compress_inp,
            compress_out=compress_out,
            compress_seed=compress_seed,
        )

        assert result == b"output"


def test_scan_cns_dependencies_resolves_recursive_reads(tmp_path):
    module_dir = tmp_path / "module"
    toppar_dir = tmp_path / "toppar"
    data_dir = tmp_path / "data"
    module_dir.mkdir()
    toppar_dir.mkdir()
    data_dir.mkdir()

    (toppar_dir / "protein.param").write_text("param", encoding="utf-8")
    (module_dir / "nested.cns").write_text('@@$param_file\n', encoding="utf-8")
    (module_dir / "read_param.cns").write_text(
        'eval ($param_file="TOPPAR:protein.param")\n@MODULE:nested.cns\n',
        encoding="utf-8",
    )
    (data_dir / "model.pdb").write_text("ATOM\n", encoding="utf-8")

    input_file = tmp_path / "job.inp"
    input_file.write_text(
        '@MODULE:read_param.cns\ncoor @@data/model.pdb\n',
        encoding="utf-8",
    )

    scan = scan_cns_dependencies(
        input_file,
        {"MODULE": str(module_dir), "TOPPAR": str(toppar_dir), "MODDIR": "."},
    )

    assert scan.unresolved_reads == []
    assert input_file.resolve() in scan.read_files
    assert (module_dir / "read_param.cns").resolve() in scan.read_files
    assert (module_dir / "nested.cns").resolve() in scan.read_files
    assert (toppar_dir / "protein.param").resolve() in scan.read_files
    assert (data_dir / "model.pdb").resolve() in scan.read_files


def test_scan_cns_dependencies_reports_unresolved_reads(tmp_path):
    input_file = tmp_path / "job.inp"
    input_file.write_text('@@$missing_file\n', encoding="utf-8")

    scan = scan_cns_dependencies(
        input_file,
        {"MODULE": str(tmp_path), "TOPPAR": str(tmp_path), "MODDIR": "."},
    )

    assert scan.read_files == [input_file.resolve()]
    assert scan.unresolved_reads == ["$missing_file"]


def test_scan_cns_dependencies_ignores_empty_optional_reads(tmp_path):
    module_dir = tmp_path / "module"
    toppar_dir = tmp_path / "toppar"
    module_dir.mkdir()
    toppar_dir.mkdir()
    input_file = tmp_path / "job.inp"
    input_file.write_text(
        'eval ($unambig_fname="")\nif ($unambig_fname # "") then\n    noe class dist @@$unambig_fname end\nend if\n',
        encoding="utf-8",
    )

    scan = scan_cns_dependencies(
        input_file,
        {"MODULE": str(module_dir), "TOPPAR": str(toppar_dir), "MODDIR": "."},
    )

    assert scan.read_files == [input_file.resolve()]
    assert scan.unresolved_reads == []


def test_scan_cns_dependencies_expands_dynamic_toppar_prefix(tmp_path):
    module_dir = tmp_path / "module"
    toppar_dir = tmp_path / "toppar"
    initial_positions = toppar_dir / "initial_positions"
    module_dir.mkdir()
    initial_positions.mkdir(parents=True)
    (initial_positions / "trans_vector_1").write_text("one\n", encoding="utf-8")
    (initial_positions / "trans_vector_2").write_text("two\n", encoding="utf-8")

    input_file = tmp_path / "job.inp"
    input_file.write_text(
        'evaluate ($filename = "TOPPAR:initial_positions/trans_vector_" + encode($n_moving_mol) )\ninline @@$filename\n',
        encoding="utf-8",
    )

    scan = scan_cns_dependencies(
        input_file,
        {"MODULE": str(module_dir), "TOPPAR": str(toppar_dir), "MODDIR": "."},
    )

    assert scan.unresolved_reads == []
    assert input_file.resolve() in scan.read_files
    assert (initial_positions / "trans_vector_1").resolve() in scan.read_files
    assert (initial_positions / "trans_vector_2").resolve() in scan.read_files


def test_cnsjob_run_seamless_requires_seamless_run(cnsjob, mocker):
    cnsjob.execution_mode = "seamless"
    cnsjob.output_file = Path("output.out")
    cnsjob.error_file = Path("output.cnserr")
    mocker.patch("haddock.libs.libsubprocess.shutil.which", return_value=None)

    with pytest.raises(Exception, match="seamless-run"):
        cnsjob.run()

