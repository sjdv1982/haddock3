"""Tests for native-cache debug tooling."""

import os
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from haddock.clis.cli_build_cache_from_seamless import main as build_cache
from haddock.core.exceptions import ConfigurationError
from haddock.libs.libcache import CacheContext, CacheRecord, append_debug_command

JOB_ONE = "a" * 64
JOB_TWO = "b" * 64
RESULT_ONE = "c" * 64
RESULT_TWO = "d" * 64


def _record(job: str, result: str, pdb: str, psf: str = "") -> CacheRecord:
    return CacheRecord(job, result, pdb, psf)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_debug_commands_write_ordered_manifests_and_identical_runner(tmp_path):
    first_run = tmp_path / "first"
    second_run = tmp_path / "second"
    first_run.mkdir()
    second_run.mkdir()
    first_context = CacheContext(first_run)

    append_debug_command(
        first_context,
        _record(JOB_ONE, RESULT_ONE, "1_topoaa/one.pdb", "1_topoaa/one.psf"),
        ("seamless-run", "-y", "one"),
        dunder={"META__FILE__canonical-cns": ["bytes", None, "e" * 64]},
    )
    append_debug_command(
        first_context,
        _record(JOB_TWO, RESULT_TWO, "2_rigidbody/two.pdb"),
        ("seamless-run", "-y", "two"),
    )
    append_debug_command(
        CacheContext(second_run),
        _record(JOB_ONE, RESULT_ONE, "1_topoaa/one.pdb"),
        ("seamless-run", "-y", "one"),
    )

    assert (first_run / "cached-commands-checksums.txt").read_text() == (
        f"{JOB_ONE}\n{JOB_TWO}\n"
    )
    assert (first_run / "cached-commands-paths.txt").read_text() == (
        f"{JOB_ONE}\t1_topoaa/one.pdb\t1_topoaa/one.psf\n"
        f"{JOB_TWO}\t2_rigidbody/two.pdb\t\n"
    )
    dunders = (first_run / "cached-commands-dry-dunders.txt").read_text().splitlines()
    assert json.loads(dunders[0]) == {
        "META__FILE__canonical-cns": ["bytes", None, "e" * 64]
    }
    assert json.loads(dunders[1]) == {}
    commands = (first_run / "cached-commands.sh").read_text()
    assert commands.count("#!/usr/bin/env bash") == 1
    assert "seamless-run -y one" in commands
    assert "seamless-run -y two" in commands

    dry_commands = (first_run / "cached-commands-dry.sh").read_text()
    assert dry_commands.count(': > "$checksums"') == 1
    assert 'seamless-run --dry --upload -y one >> "$checksums"' in dry_commands
    assert 'seamless-run --dry --upload -y two >> "$checksums"' in dry_commands

    runner = first_run / "run-cached-commands-dry-checksums.sh"
    assert os.access(runner, os.X_OK)
    assert (
        runner.read_bytes()
        == (second_run / "run-cached-commands-dry-checksums.sh").read_bytes()
    )


def test_generated_dry_script_captures_exact_job_checksums(tmp_path):
    append_debug_command(
        CacheContext(tmp_path),
        _record(JOB_ONE, RESULT_ONE, "one.pdb"),
        ("seamless-run", JOB_ONE),
    )
    append_debug_command(
        CacheContext(tmp_path),
        _record(JOB_TWO, RESULT_TWO, "two.pdb"),
        ("seamless-run", JOB_TWO),
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "seamless-run",
        "#!/bin/sh\n"
        '[ "$1" = --dry ] || exit 3\n'
        '[ "$2" = --upload ] || exit 4\n'
        "printf '%s\\n' \"$3\"\n",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"

    subprocess.run(
        [tmp_path / "cached-commands-dry.sh"],
        cwd=tmp_path,
        env=environment,
        check=True,
    )

    assert (tmp_path / "cached-commands-dry-checksums.txt").read_bytes() == (
        tmp_path / "cached-commands-checksums.txt"
    ).read_bytes()


def test_generated_runner_executes_checksums_in_parallel(tmp_path):
    append_debug_command(
        CacheContext(tmp_path),
        _record(JOB_ONE, RESULT_ONE, "one.pdb"),
        ("seamless-run", JOB_ONE),
    )
    (tmp_path / "cached-commands-dry-checksums.txt").write_text(
        f"{JOB_ONE}\n{JOB_TWO}\n", encoding="utf-8"
    )
    (tmp_path / "cached-commands-dry-dunders.txt").write_text(
        "{}\n{}\n", encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "seamless-run-transformation",
        "#!/bin/sh\nprintf 'result-%s\\n' \"$1\"\n",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"

    completed = subprocess.run(
        [tmp_path / "run-cached-commands-dry-checksums.sh", "2"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert sorted(completed.stdout.splitlines()) == sorted(
        [
            f"{JOB_ONE}\tresult-{JOB_ONE}",
            f"{JOB_TWO}\tresult-{JOB_TWO}",
        ]
    )

    _write_executable(
        bin_dir / "seamless-run-transformation",
        "#!/bin/sh\nexit 9\n",
    )
    failed = subprocess.run(
        [tmp_path / "run-cached-commands-dry-checksums.sh", "2"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
    )
    assert failed.returncode != 0


def _make_database(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE transformation (checksum TEXT PRIMARY KEY, result TEXT)"
        )
        connection.executemany(
            "INSERT INTO transformation (checksum, result) VALUES (?, ?)", rows
        )


def test_build_cache_from_seamless_reproduces_native_cache(
    tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "cached-commands-checksums.txt").write_text(
        f"{JOB_ONE}\n{JOB_TWO}\n", encoding="utf-8"
    )
    (run_dir / "cached-commands-paths.txt").write_text(
        f"{JOB_ONE}\t1_topoaa/one.pdb\t1_topoaa/one.psf\n"
        f"{JOB_TWO}\t2_rigidbody/two.pdb\t\n",
        encoding="utf-8",
    )
    seamless_cache = tmp_path / "seamless-cache"
    _make_database(
        seamless_cache / "__TOPLEVEL__" / "seamless.db",
        [(JOB_ONE, RESULT_ONE), (JOB_TWO, RESULT_TWO)],
    )
    native_cache = (
        f"{JOB_ONE}\t{RESULT_ONE}\t1_topoaa/one.pdb\t1_topoaa/one.psf\n"
        f"{JOB_TWO}\t{RESULT_TWO}\t2_rigidbody/two.pdb\t\n"
    )
    (run_dir / "CACHE").write_text(native_cache, encoding="utf-8")
    monkeypatch.chdir(run_dir)
    monkeypatch.setenv("SEAMLESS_CACHE", str(seamless_cache))

    output = build_cache(Path("CACHE.from-seamless"))

    assert output == Path("CACHE.from-seamless")
    assert output.read_text(encoding="utf-8") == native_cache
    assert "Wrote 2 CACHE records" in capsys.readouterr().out


def test_build_cache_requires_every_transformation_result(tmp_path, monkeypatch):
    (tmp_path / "cached-commands-checksums.txt").write_text(
        f"{JOB_ONE}\n", encoding="utf-8"
    )
    (tmp_path / "cached-commands-paths.txt").write_text(
        f"{JOB_ONE}\tone.pdb\t\n", encoding="utf-8"
    )
    seamless_cache = tmp_path / "seamless-cache"
    _make_database(seamless_cache / "__TOPLEVEL__" / "seamless.db", [])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEAMLESS_CACHE", str(seamless_cache))

    with pytest.raises(ConfigurationError, match="has no result"):
        build_cache(Path("rebuilt-cache"))

    assert not Path("rebuilt-cache").exists()
