from pathlib import Path
from unittest.mock import MagicMock

from haddock.libs.libcnsoutput import normalize_cns_pdb
from haddock.libs.libsubprocess import CNSJob


def test_normalize_cns_pdb_removes_run_volatile_remarks(tmp_path):
    pdb = tmp_path / "model.pdb"
    pdb.write_text(
        "\n".join(
            [
                "REMARK FILENAME= /tmp/run_1/model.pdb",
                "REMARK initial structure 1 - ../run_1/input.pdb",
                "REMARK DATE: 27-Jun-2026 12:34:56",
                "REMARK score: 1.23",
                "ATOM      1  CA  ALA A   1       0.000   0.000   0.000",
                "END",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    changed = normalize_cns_pdb(pdb)

    assert changed is True
    assert pdb.read_text(encoding="utf-8") == (
        "REMARK score: 1.23\n"
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n"
        "END\n"
    )


def test_normalize_cns_pdb_leaves_stable_file_unchanged(tmp_path):
    pdb = tmp_path / "model.pdb"
    content = "REMARK score: 1.23\nEND\n"
    pdb.write_text(content, encoding="utf-8")

    changed = normalize_cns_pdb(pdb)

    assert changed is False
    assert pdb.read_text(encoding="utf-8") == content


def test_cnsjob_run_normalizes_output_pdb(monkeypatch, tmp_path):
    output_pdb = tmp_path / "model.pdb"
    output_pdb.write_text(
        "REMARK DATE: volatile\n"
        "REMARK initial structure 1 - ../run_1/input.pdb\n"
        "REMARK score: 1.23\n",
        encoding="utf-8",
    )
    job = CNSJob(
        input_file="cns input stream",
        cns_exec=_executable(tmp_path),
        output_pdb_files=[output_pdb],
    )
    _mock_popen(monkeypatch)

    job.run()

    assert output_pdb.read_text(encoding="utf-8") == "REMARK score: 1.23\n"


def _executable(tmp_path: Path) -> Path:
    path = tmp_path / "cns"
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _mock_popen(monkeypatch) -> None:
    popen = MagicMock()
    popen.return_value.communicate.return_value = (b"output", None)
    monkeypatch.setattr("subprocess.Popen", popen)
