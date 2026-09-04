"""Run-local CNS cache records and CLI context.

This module owns cache policy and on-disk record validation.  It deliberately
does not import Seamless; checksum construction remains in ``libseamless``.
"""

from __future__ import annotations

import errno
import gzip
import json
import os
import re
import shlex
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from haddock.core.exceptions import ConfigurationError
from haddock.core.typing import ArgumentParser
from haddock.libs.libcnsoutput import is_normalized_cns_artifact

_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")
_FAILED = "FAILED"
_RUN_DRY_CHECKSUMS_SCRIPT = b"""#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! $1 =~ ^[1-9][0-9]*$ ]]; then
    echo "usage: $0 NPARALLEL" >&2
    exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
checksum_file="$script_dir/cached-commands-dry-checksums.txt"
dunder_file="$script_dir/cached-commands-dry-dunders.txt"
if [[ ! -f "$checksum_file" ]]; then
    echo "missing checksum file: $checksum_file" >&2
    exit 1
fi
if [[ ! -f "$dunder_file" ]]; then
    echo "missing dunder file: $dunder_file" >&2
    exit 1
fi
if [[ ! -s "$checksum_file" ]]; then
    exit 0
fi
if [[ $(wc -l < "$checksum_file") -ne $(wc -l < "$dunder_file") ]]; then
    echo "checksum and dunder files have different line counts" >&2
    exit 1
fi

paste "$checksum_file" "$dunder_file" |
while IFS=$'\t' read -r checksum dunder; do
    printf '%s\\0%s\\0' "$checksum" "$dunder"
done |
xargs -0 -P "$1" -n 2 bash -euc '
    checksum=$1
    dunder=$2
    result=$(seamless-run-transformation "$checksum" \
        --dunder <(printf "%s\\n" "$dunder"))
    printf "%s\\t%s\\n" "$checksum" "$result"
' _
"""


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
    source_indexes: tuple[CacheIndex, ...] = ()


def add_cache_arg(parser: ArgumentParser) -> None:
    """Add repeatable native cache-source options to the main HADDOCK CLI."""
    parser.add_argument(
        "--cache",
        type=Path,
        action="append",
        default=[],
        metavar="RUN_DIR",
        help="Read verified local CNS results from a previous run directory; repeatable.",
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


def source_record_artifact_paths(index: CacheIndex, record: CacheRecord) -> tuple[Path, ...]:
    """Resolve every source artifact named by a successful CACHE record."""
    paths = [record.pdb_path] + ([record.psf_path] if record.psf_path else [])
    return tuple(_resolve_source_artifact(index, path) for path in paths)


def source_cache_has_pdb_path(index: CacheIndex, relative: Path) -> bool:
    """Return whether a successful CACHE record declares this PDB path.

    This is deliberately only a cheap scheduling hint.  It does not consult
    artifacts, calculate checksums, or touch the source filesystem; the
    worker's normal cache lookup and verification remain authoritative.
    """
    return any(
        record.result_checksum != _FAILED
        and record.pdb_path == relative.as_posix()
        for record in index.records.values()
    )


def append_cache_record(
    context: CacheContext,
    job_checksum: str,
    result_checksum: str,
    pdb_path: Path,
    psf_path: Path | None = None,
) -> CacheRecord:
    """Append one complete record from the main-process cache writer."""
    record = CacheRecord(
        job_checksum=job_checksum,
        result_checksum=result_checksum,
        pdb_path=_run_relative_path(context.current_run, pdb_path),
        psf_path="" if psf_path is None else _run_relative_path(context.current_run, psf_path),
    )
    _validate_record(record, 0)
    payload = format_cache_record(record).encode("utf-8")
    cache_file = context.current_run / "CACHE"
    descriptor = os.open(cache_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        _write_all(descriptor, payload)
    finally:
        os.close(descriptor)
    return record


def format_cache_record(record: CacheRecord, line_number: int = 0) -> str:
    """Validate and format one native four-field ``CACHE`` record."""
    _validate_record(record, line_number)
    return (
        f"{record.job_checksum}\t{record.result_checksum}\t"
        f"{record.pdb_path}\t{record.psf_path}\n"
    )


def verify_and_restore(
    source_index: CacheIndex,
    record: CacheRecord,
    destinations: tuple[Path, ...],
    checksum_for_paths,
) -> str | None:
    """Stage, verify and atomically restore cached artifacts.

    Returned text is a miss reason.  A successful restore returns ``None``.
    Cache-restored hardlinks may share an inode with the source run: callers
    must never normalize or otherwise modify them in place.
    """
    source_paths = [record.pdb_path] + ([record.psf_path] if record.psf_path else [])
    if len(source_paths) != len(destinations):
        return "record output arity differs from this job"
    temporary_paths: list[Path] = []
    materialized_paths: list[Path] = []
    try:
        for source, destination in zip(
            source_record_artifact_paths(source_index, record), destinations
        ):
            temporary_paths.append(_stage_source_artifact(source, destination))
        unnormalized = next(
            (
                Path(declared).name
                for declared, staged in zip(source_paths, temporary_paths)
                if not is_normalized_cns_artifact(staged, Path(declared).name)
            ),
            None,
        )
        if unnormalized is not None:
            return f"cached artifact is not normalized: {unnormalized}"
        if checksum_for_paths(tuple(temporary_paths)) != record.result_checksum:
            return "artifact checksum differs from CACHE record"
        for temporary, destination in zip(temporary_paths, destinations):
            materialized_paths.append(
                _materialize_compressed_artifact(temporary, destination)
            )
        for temporary, materialized, destination in zip(
            temporary_paths, materialized_paths, destinations
        ):
            destination.parent.mkdir(parents=True, exist_ok=True)
            restored = (
                Path(f"{destination}{temporary.suffix}")
                if temporary.suffix in (".gz", ".zst")
                else destination
            )
            os.replace(temporary, restored)
            if materialized != temporary:
                os.replace(materialized, destination)
        return None
    except ConfigurationError:
        raise
    except (OSError, ValueError, EOFError) as error:
        return str(error)
    finally:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)
        for materialized in materialized_paths:
            materialized.unlink(missing_ok=True)


def _resolve_source_artifact(index: CacheIndex, relative: str) -> Path:
    root = index.source_run.resolve()
    candidate = root / relative
    options = (candidate, Path(f"{candidate}.gz"), Path(f"{candidate}.zst"))
    for option in options:
        if not option.exists():
            continue
        resolved = option.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError(f"source artifact escapes source run: {relative}")
        if not resolved.is_file():
            raise ValueError(f"source artifact is not a file: {relative}")
        return resolved
    raise FileNotFoundError(f"source artifact is missing: {relative}")


def _stage_source_artifact(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    compression_suffix = source.suffix if source.suffix in (".gz", ".zst") else ""
    temporary = destination.parent / (
        f".{destination.name}.cache-{uuid.uuid4().hex}{compression_suffix}"
    )
    hardlink = _hardlink_policy()
    if hardlink is not False:
        try:
            os.link(source, temporary)
            return temporary
        except OSError as error:
            temporary.unlink(missing_ok=True)
            if hardlink is True:
                raise ConfigurationError(
                    "HADDOCK_CACHE_HARDLINK=1 requires a hardlinkable cache artifact"
                ) from error
            if error.errno not in (errno.EXDEV, errno.EPERM, errno.EMLINK, errno.EACCES):
                # A copy is also a safe fallback for filesystems that do not
                # support links, but keep unexpected source errors visible.
                if not source.is_file():
                    raise
    shutil.copy2(source, temporary)
    return temporary


def _hardlink_policy() -> bool | None:
    """Return forced hardlink/copy policy, or best effort when unset."""
    value = os.environ.get("HADDOCK_CACHE_HARDLINK")
    if value is None:
        return None
    if value == "1":
        return True
    if value == "0":
        return False
    raise ConfigurationError(
        "HADDOCK_CACHE_HARDLINK must be unset, '0', or '1'; "
        f"got {value!r}"
    )


def validate_hardlink_policy() -> None:
    """Validate the cache materialization policy before worker startup."""
    _hardlink_policy()


def _materialize_compressed_artifact(staged: Path, destination: Path) -> Path:
    """Create the logical plain working file while retaining compressed storage."""
    if staged.suffix not in (".gz", ".zst"):
        return staged
    materialized = destination.parent / (
        f".{destination.name}.cache-materialized-{uuid.uuid4().hex}"
    )
    if staged.suffix == ".gz":
        with gzip.open(staged, "rb") as compressed, open(materialized, "wb") as output:
            shutil.copyfileobj(compressed, output)
    else:
        import zstandard

        with open(staged, "rb") as compressed, open(materialized, "wb") as output:
            zstandard.ZstdDecompressor().copy_stream(compressed, output)
    return materialized


def _run_relative_path(run_dir: Path, path: Path) -> str:
    path = path.resolve()
    try:
        return path.relative_to(run_dir.resolve()).as_posix()
    except ValueError as error:
        raise ConfigurationError(f"Cache artifact is outside current run: {path}") from error


def append_debug_command(
    context: CacheContext,
    record: CacheRecord,
    command: tuple[str, ...],
    *,
    dunder: dict | None = None,
) -> None:
    """Append one ordered set of replay and cache-reconstruction metadata."""
    _validate_record(record, 0)
    if not command:
        raise ConfigurationError("Empty cached Seamless command")

    run_dir = context.current_run
    comment = f"# job={record.job_checksum} result={record.result_checksum}\n"
    quoted_command = " ".join(shlex.quote(part) for part in command)
    _append_debug_file(
        run_dir / "cached-commands.sh",
        b"#!/usr/bin/env bash\n",
        (comment + quoted_command + "\n").encode("utf-8"),
        executable=True,
    )
    _append_debug_file(
        run_dir / "cached-commands-checksums.txt",
        b"",
        f"{record.job_checksum}\n".encode("ascii"),
    )
    _append_debug_file(
        run_dir / "cached-commands-paths.txt",
        b"",
        (f"{record.job_checksum}\t{record.pdb_path}\t{record.psf_path}\n").encode(),
    )
    _append_debug_file(
        run_dir / "cached-commands-dry-dunders.txt",
        b"",
        (json.dumps(dunder or {}, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        ),
    )

    # Seamless prints the dry-run transformation checksum only when the
    # transformation buffers are uploaded (or a job directory is requested).
    dry_command = (command[0], "--dry", "--upload", *command[1:])
    quoted_dry_command = " ".join(shlex.quote(part) for part in dry_command)
    _append_debug_file(
        run_dir / "cached-commands-dry.sh",
        (
            b"#!/usr/bin/env bash\n"
            b"set -euo pipefail\n"
            b"script_dir=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd -P)\n"
            b"checksums=\"$script_dir/cached-commands-dry-checksums.txt\"\n"
            b": > \"$checksums\"\n"
        ),
        (comment + quoted_dry_command + ' >> "$checksums"\n').encode("utf-8"),
        executable=True,
    )
    _ensure_identical_script(
        run_dir / "run-cached-commands-dry-checksums.sh",
        _RUN_DRY_CHECKSUMS_SCRIPT,
    )


def _append_debug_file(
    path: Path,
    header: bytes,
    payload: bytes,
    *,
    executable: bool = False,
) -> None:
    """Atomically append one payload, initializing an optional header."""
    mode = 0o755 if executable else 0o644
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, mode)
    try:
        if os.fstat(descriptor).st_size == 0 and header:
            _write_all(descriptor, header)
        _write_all(descriptor, payload)
    finally:
        os.close(descriptor)
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def _ensure_identical_script(path: Path, content: bytes) -> None:
    """Create a fixed helper script, rejecting incompatible stale content."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ConfigurationError(f"Existing debug helper has unexpected content: {path}")
        path.chmod(path.stat().st_mode | 0o111)
        return
    try:
        _write_all(descriptor, content)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        offset += os.write(descriptor, payload[offset:])


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
