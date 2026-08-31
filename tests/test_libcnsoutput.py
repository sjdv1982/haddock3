from pathlib import Path
from unittest.mock import MagicMock

from haddock.libs.libcnsoutput import (
    is_normalized_cns_pdb,
    normalize_cns_pdb,
)
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


def test_normalize_cns_pdb_does_not_mutate_hardlink_source(tmp_path):
    source = tmp_path / "source.pdb"
    destination = tmp_path / "destination.pdb"
    source.write_text("REMARK DATE: volatile\nATOM\n", encoding="utf-8")
    destination.hardlink_to(source)

    assert normalize_cns_pdb(destination) is True
    assert source.read_text(encoding="utf-8") == "REMARK DATE: volatile\nATOM\n"
    assert destination.read_text(encoding="utf-8") == "ATOM\n"


def test_is_normalized_cns_pdb(tmp_path):
    pdb = tmp_path / "model.pdb"
    pdb.write_text("REMARK DATE: volatile\nATOM\n", encoding="utf-8")

    assert is_normalized_cns_pdb(pdb) is False
    normalize_cns_pdb(pdb)
    assert is_normalized_cns_pdb(pdb) is True


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


PSF_WITH_DATE = """data_cns_mtf

_cns_mtf.title
; FILENAME="model_haddock.psf"
  disulphide added: from A    6    to A    127
  DATE:31-Aug-2026  01:17:08       created by user: unknown
  VERSION:1.3U
;

_cns_mtf.id   1
"""


def test_psf_normalization_removes_only_the_date_stamp(tmp_path):
    """CNS stamps the wall-clock time into every PSF it writes.

    Two runs of the same topology then differ in that one line and nothing
    else -- and since every downstream CNS job reads the PSF, that makes the
    topology non-reproducible and everything computed from it unshareable
    between runs.
    """
    from haddock.libs.libcnsoutput import (
        is_normalized_cns_psf,
        normalize_cns_psf,
        normalize_cns_psf_bytes,
    )

    path = tmp_path / "model_haddock.psf"
    path.write_text(PSF_WITH_DATE, encoding="utf-8")
    assert not is_normalized_cns_psf(path)

    assert normalize_cns_psf(path) is True
    text = path.read_text(encoding="utf-8")

    assert "created by user" not in text
    # The title block is free text delimited by ';' lines, so dropping one
    # line from it must leave the file well formed and everything else intact.
    assert text.splitlines() == [
        "data_cns_mtf",
        "",
        "_cns_mtf.title",
        '; FILENAME="model_haddock.psf"',
        "  disulphide added: from A    6    to A    127",
        "  VERSION:1.3U",
        ";",
        "",
        "_cns_mtf.id   1",
    ]
    assert is_normalized_cns_psf(path)
    assert normalize_cns_psf(path) is False


def test_psf_normalization_makes_two_runs_agree(tmp_path):
    """The property the fix exists for: same topology, same bytes."""
    from haddock.libs.libcnsoutput import normalize_cns_psf_bytes

    monday = PSF_WITH_DATE.encode("utf-8")
    tuesday = PSF_WITH_DATE.replace("01:17:08", "09:42:55").encode("utf-8")

    assert monday != tuesday
    assert normalize_cns_psf_bytes(monday) == normalize_cns_psf_bytes(tuesday)


def test_psf_normalization_does_not_touch_structural_data(tmp_path):
    """Both markers are required, so structural data is never mistaken for it."""
    from haddock.libs.libcnsoutput import normalize_cns_psf_bytes

    #  A data line that happens to begin with the same token must survive.
    body = "  DATE: 1 2 3 4\n  1 A    6    CYS  SG   SG     0.000000\n"
    assert normalize_cns_psf_bytes(body.encode()) == body.encode()


def test_artifact_normalization_dispatches_on_suffix(tmp_path):
    """The cache checks artifacts without having to know which kind they are."""
    from haddock.libs.libcnsoutput import is_normalized_cns_artifact

    psf = tmp_path / "model_haddock.psf"
    psf.write_text(PSF_WITH_DATE, encoding="utf-8")
    assert not is_normalized_cns_artifact(psf)

    pdb = tmp_path / "model.pdb"
    pdb.write_text("REMARK DATE:31-Aug-2026\nATOM      1  N\nEND\n", encoding="utf-8")
    assert not is_normalized_cns_artifact(pdb)

    other = tmp_path / "model.out"
    other.write_text("anything at all\n", encoding="utf-8")
    assert is_normalized_cns_artifact(other)
