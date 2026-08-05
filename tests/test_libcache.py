"""Tests for strict native CACHE parsing."""

from pathlib import Path

import pytest

from haddock.core.exceptions import ConfigurationError
from haddock.libs.libcache import parse_cache


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
