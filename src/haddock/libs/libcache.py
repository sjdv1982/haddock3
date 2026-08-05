"""Run-local CNS cache records and CLI context.

This module owns cache policy and on-disk record validation.  It deliberately
does not import Seamless; checksum construction remains in ``libseamless``.
"""

from __future__ import annotations

import re
import os
import fcntl
from dataclasses import dataclass
from pathlib import Path

from haddock.core.exceptions import ConfigurationError
from haddock.core.typing import ArgumentParser


_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")
_FAILED = "FAILED"


@dataclass(frozen=True)
class CacheRecord:
    """One four-field line in a run's ``CACHE`` file."""

    job_checksum: str
    result_checksum: str
    pdb_path: str
    psf_path: str


@dataclass(frozen=True)
class CacheIndex:
    """Read-only-by-contract source records parsed once by the CLI."""

    source_run: Path
    records: dict[str, CacheRecord]


@dataclass(frozen=True)
class CacheContext:
    """Pickleable cache state explicitly propagated to local CNS jobs."""

    current_run: Path
    source_index: CacheIndex | None


def add_cache_arg(parser: ArgumentParser) -> None:
    """Add the native cache source option to the main HADDOCK CLI."""
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        metavar="RUN_DIR",
        help="Read verified local CNS results from a previous run directory.",
    )


def validate_cache_source(source: Path) -> Path:
    """Return a resolved source run after strict pre-setup validation."""
    source = source.resolve()
    if not source.is_dir():
        raise ConfigurationError(f"Cache source is not a directory: {source}")
    cache_file = source / "CACHE"
    if not cache_file.is_file():
        raise ConfigurationError(f"Cache source has no regular CACHE file: {source}")
    return source


def parse_cache(source_run: Path) -> CacheIndex:
    """Parse CACHE once, rejecting malformed and conflicting records."""
    source_run = validate_cache_source(source_run)
    cache_path = source_run / "CACHE"
    content = cache_path.read_text(encoding="utf-8")
    if content and not content.endswith("\n"):
        raise ConfigurationError(f"CACHE ends with a truncated record: {cache_path}")
    records: dict[str, CacheRecord] = {}
    line_numbers: dict[str, int] = {}
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line:
            raise ConfigurationError(f"Blank CACHE record at line {line_number}: {cache_path}")
        fields = line.split("\t")
        if len(fields) != 4:
            raise ConfigurationError(f"CACHE line {line_number} does not have four fields")
        record = CacheRecord(*fields)
        _validate_record(record, line_number)
        existing = records.get(record.job_checksum)
        if existing is None:
            records[record.job_checksum] = record
            line_numbers[record.job_checksum] = line_number
            continue
        if _arity(existing) != _arity(record):
            raise ConfigurationError(
                f"CACHE line {line_number} changes output arity of job {record.job_checksum}"
            )
        if existing.result_checksum != record.result_checksum:
            raise ConfigurationError(
                f"Conflicting CACHE records for {record.job_checksum} at lines "
                f"{line_numbers[record.job_checksum]} and {line_number}: {existing!r} / {record!r}"
            )
    return CacheIndex(source_run=source_run, records=records)


def lookup_cache_record(index: CacheIndex | None, job_checksum: str) -> CacheRecord | None:
    """Look up a record without mutating the source index."""
    return None if index is None else index.records.get(job_checksum)


def append_cache_record(
    context: CacheContext,
    job_checksum: str,
    result_checksum: str,
    pdb_path: Path,
    psf_path: Path | None = None,
) -> CacheRecord:
    """Append one complete record under an inter-process advisory lock."""
    record = CacheRecord(
        job_checksum=job_checksum,
        result_checksum=result_checksum,
        pdb_path=_run_relative_path(context.current_run, pdb_path),
        psf_path="" if psf_path is None else _run_relative_path(context.current_run, psf_path),
    )
    _validate_record(record, 0)
    payload = (
        f"{record.job_checksum}\t{record.result_checksum}\t{record.pdb_path}\t{record.psf_path}\n"
    ).encode("utf-8")
    cache_file = context.current_run / "CACHE"
    descriptor = os.open(cache_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    return record


def _run_relative_path(run_dir: Path, path: Path) -> str:
    path = path.resolve()
    try:
        return path.relative_to(run_dir.resolve()).as_posix()
    except ValueError as error:
        raise ConfigurationError(f"Cache artifact is outside current run: {path}") from error


def _validate_record(record: CacheRecord, line_number: int) -> None:
    if not _CHECKSUM.fullmatch(record.job_checksum):
        raise ConfigurationError(f"Invalid job checksum at CACHE line {line_number}")
    if record.result_checksum != _FAILED and not _CHECKSUM.fullmatch(record.result_checksum):
        raise ConfigurationError(f"Invalid result checksum at CACHE line {line_number}")
    if record.result_checksum == _FAILED:
        if not record.pdb_path:
            raise ConfigurationError(f"FAILED CACHE record lacks PDB path at line {line_number}")
    elif not record.pdb_path:
        raise ConfigurationError(f"Successful CACHE record lacks PDB path at line {line_number}")
    for path in (record.pdb_path, record.psf_path):
        if path:
            _validate_relative_path(path, line_number)


def _validate_relative_path(path: str, line_number: int) -> None:
    candidate = Path(path)
    if "\n" in path or "\t" in path or candidate.is_absolute() or ".." in candidate.parts:
        raise ConfigurationError(f"Unsafe CACHE path at line {line_number}: {path!r}")
    if candidate.as_posix() != path or path in ("", "."):
        raise ConfigurationError(f"Non-normalized CACHE path at line {line_number}: {path!r}")


def _arity(record: CacheRecord) -> int:
    return 2 if record.psf_path else 1
