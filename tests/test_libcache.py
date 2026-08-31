"""Tests for strict native CACHE parsing."""

from pathlib import Path
import gzip

import zstandard

import pytest

from haddock.core.exceptions import ConfigurationError
from haddock.libs.libcache import CacheIndex, CacheRecord, parse_cache, verify_and_restore


JOB = "a" * 64
RESULT = "b" * 64


def _cache(tmp_path: Path, text: str) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "CACHE").write_text(text, encoding="utf-8")
    return run


def test_parse_cache_retains_first_identical_duplicate(tmp_path):
    run = _cache(
        tmp_path,
        f"{JOB}\t{RESULT}\t1_step/model.pdb\t\n"
        f"{JOB}\t{RESULT}\tother/model.pdb\t\n",
    )
    index = parse_cache(run)

    assert index.records[JOB].pdb_path == "1_step/model.pdb"


@pytest.mark.parametrize(
    "line",
    [
        f"{JOB}\t{RESULT}\t../escape.pdb\t\n",
        f"{JOB}\t{RESULT}\t/absolute.pdb\t\n",
        f"{JOB.upper()}\t{RESULT}\tmodel.pdb\t\n",
        f"{JOB}\t{RESULT}\t\t\n",
    ],
)
def test_parse_cache_rejects_invalid_records(tmp_path, line):
    with pytest.raises(ConfigurationError):
        parse_cache(_cache(tmp_path, line))


def test_restore_hardlinks_gzip_and_materializes_working_artifact(tmp_path):
    source = tmp_path / "source"
    current = tmp_path / "current"
    source.mkdir()
    current.mkdir()
    artifact = source / "1_topoaa" / "model.pdb.gz"
    artifact.parent.mkdir()
    with gzip.open(artifact, "wb") as handle:
        handle.write(b"PDB\n")
    record = CacheRecord(JOB, RESULT, "1_topoaa/model.pdb", "")
    destination = current / "2_topoaa" / "model.pdb"

    reason = verify_and_restore(
        CacheIndex(source, {JOB: record}),
        record,
        (destination,),
        lambda paths: RESULT,
    )

    assert reason is None
    assert destination.read_bytes() == b"PDB\n"
    restored_compressed = Path(f"{destination}.gz")
    assert restored_compressed.samefile(artifact)


def test_restore_hardlinks_zstd_and_materializes_working_artifact(tmp_path):
    source = tmp_path / "source"
    current = tmp_path / "current"
    source.mkdir()
    current.mkdir()
    artifact = source / "1_topoaa" / "model.pdb.zst"
    artifact.parent.mkdir()
    artifact.write_bytes(zstandard.ZstdCompressor().compress(b"PDB\n"))
    record = CacheRecord(JOB, RESULT, "1_topoaa/model.pdb", "")
    destination = current / "2_topoaa" / "model.pdb"

    reason = verify_and_restore(
        CacheIndex(source, {JOB: record}),
        record,
        (destination,),
        lambda paths: RESULT,
    )

    assert reason is None
    assert destination.read_bytes() == b"PDB\n"
    restored_compressed = Path(f"{destination}.zst")
    assert restored_compressed.samefile(artifact)


def test_restore_hardlinks_compressed_pdb_and_psf_outputs(tmp_path):
    source = tmp_path / "source"
    current = tmp_path / "current"
    source_step = source / "1_topoaa"
    source_step.mkdir(parents=True)
    current.mkdir()
    pdb = source_step / "model.pdb.gz"
    psf = source_step / "model.psf.gz"
    with gzip.open(pdb, "wb") as output:
        output.write(b"PDB\n")
    with gzip.open(psf, "wb") as output:
        output.write(b"PSF\n")
    record = CacheRecord(
        JOB,
        RESULT,
        "1_topoaa/model.pdb",
        "1_topoaa/model.psf",
    )
    pdb_destination = current / "2_topoaa" / "model.pdb"
    psf_destination = current / "2_topoaa" / "model.psf"

    reason = verify_and_restore(
        CacheIndex(source, {JOB: record}),
        record,
        (pdb_destination, psf_destination),
        lambda paths: RESULT,
    )

    assert reason is None
    assert pdb_destination.read_bytes() == b"PDB\n"
    assert psf_destination.read_bytes() == b"PSF\n"
    assert Path(f"{pdb_destination}.gz").samefile(pdb)
    assert Path(f"{psf_destination}.gz").samefile(psf)


def test_restore_rejects_unnormalized_pdb_artifact(tmp_path):
    source = tmp_path / "source"
    current = tmp_path / "current"
    source.mkdir()
    current.mkdir()
    artifact = source / "1_topoaa" / "model.pdb"
    artifact.parent.mkdir()
    artifact.write_text("REMARK DATE: volatile\nPDB\n", encoding="utf-8")
    record = CacheRecord(JOB, RESULT, "1_topoaa/model.pdb", "")
    destination = current / "2_topoaa" / "model.pdb"

    reason = verify_and_restore(
        CacheIndex(source, {JOB: record}),
        record,
        (destination,),
        lambda paths: RESULT,
    )

    assert reason == "cached artifact is not normalized: model.pdb"
    assert not destination.exists()


def test_restore_rejects_unnormalized_psf_artifact(tmp_path):
    """A topology is two files, and both have to be stable.

    CNS stamps the wall-clock time into a PSF's title, and every downstream
    job reads the PSF. Serving one that still carries the stamp would hand a
    later job an input that no fresh run can reproduce.
    """
    source = tmp_path / "source"
    current = tmp_path / "current"
    source.mkdir()
    current.mkdir()
    pdb = source / "1_topoaa" / "model.pdb"
    pdb.parent.mkdir()
    pdb.write_text("PDB\n", encoding="utf-8")
    psf = source / "1_topoaa" / "model.psf"
    psf.write_text(
        "_cns_mtf.title\n; FILENAME=\"model.psf\"\n"
        "  DATE:31-Aug-2026  01:17:08       created by user: unknown\n;\n",
        encoding="utf-8",
    )
    record = CacheRecord(JOB, RESULT, "1_topoaa/model.pdb", "1_topoaa/model.psf")

    reason = verify_and_restore(
        CacheIndex(source, {JOB: record}),
        record,
        (current / "2_topoaa" / "model.pdb", current / "2_topoaa" / "model.psf"),
        lambda paths: RESULT,
    )

    assert reason == "cached artifact is not normalized: model.psf"
