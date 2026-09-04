"""Build a native HADDOCK CACHE file from Seamless transformation results.

Run this command from a debug-mode HADDOCK run directory after its generated
Seamless transformations have completed.  ``SEAMLESS_CACHE`` must name the
cache containing their ``seamless.db`` database.

Usage::

    build-cache-from-seamless
    build-cache-from-seamless CACHE.from-seamless
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from haddock.core.exceptions import ConfigurationError
from haddock.libs.libcache import CacheRecord, format_cache_record

CHECKSUMS_FILE = "cached-commands-checksums.txt"
PATHS_FILE = "cached-commands-paths.txt"

ap = argparse.ArgumentParser(
    description=__doc__,
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
ap.add_argument(
    "cache_file",
    nargs="?",
    type=Path,
    default=Path("CACHE"),
    metavar="CACHE-FILE",
    help="output CACHE file (default: CACHE)",
)


def _read_lines(path: Path) -> list[str]:
    """Read a complete, nonblank line manifest."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(
            f"Cannot read debug manifest {path}: {error}"
        ) from error
    if content and not content.endswith("\n"):
        raise ConfigurationError(f"Debug manifest ends with a truncated line: {path}")
    lines = content.splitlines()
    if any(not line for line in lines):
        raise ConfigurationError(f"Debug manifest contains a blank line: {path}")
    return lines


def _read_manifest(run_dir: Path) -> list[tuple[str, str, str]]:
    """Join ordered checksum and output-path manifests with strict validation."""
    checksums = _read_lines(run_dir / CHECKSUMS_FILE)
    path_lines = _read_lines(run_dir / PATHS_FILE)
    if len(checksums) != len(path_lines):
        raise ConfigurationError(
            f"Debug manifests have different record counts: "
            f"{len(checksums)} checksums and {len(path_lines)} paths"
        )

    rows: list[tuple[str, str, str]] = []
    for line_number, (checksum, path_line) in enumerate(
        zip(checksums, path_lines), start=1
    ):
        fields = path_line.split("\t")
        if len(fields) != 3:
            raise ConfigurationError(
                f"{PATHS_FILE} line {line_number} does not have three fields"
            )
        path_checksum, pdb_path, psf_path = fields
        if path_checksum != checksum:
            raise ConfigurationError(
                f"Debug manifests disagree at line {line_number}: "
                f"{checksum!r} != {path_checksum!r}"
            )
        # Validate the checksum and paths now; the actual result is joined below.
        format_cache_record(
            CacheRecord(checksum, "FAILED", pdb_path, psf_path), line_number
        )
        rows.append((checksum, pdb_path, psf_path))
    return rows


def _seamless_database() -> Path:
    """Resolve the effective top-level Seamless SQLite database."""
    cache_setting = os.environ.get("SEAMLESS_CACHE")
    if not cache_setting:
        raise ConfigurationError("SEAMLESS_CACHE is not set")
    cache_dir = Path(cache_setting).expanduser().resolve()
    candidates = (
        cache_dir / "__TOPLEVEL__" / "seamless.db",
        cache_dir / "seamless.db",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ConfigurationError(
        "Cannot find seamless.db in SEAMLESS_CACHE; checked "
        + " and ".join(str(candidate) for candidate in candidates)
    )


def _cache_text(rows: list[tuple[str, str, str]], database: Path) -> str:
    """Look up each transformation result and return native CACHE text."""
    records: list[str] = []
    try:
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise ConfigurationError(
            f"Cannot open Seamless database {database}: {error}"
        ) from error
    try:
        for line_number, (checksum, pdb_path, psf_path) in enumerate(rows, start=1):
            try:
                row = connection.execute(
                    "SELECT result FROM transformation WHERE checksum = ?",
                    (checksum,),
                ).fetchone()
            except sqlite3.Error as error:
                raise ConfigurationError(
                    f"Cannot query Seamless transformation database: {error}"
                ) from error
            if row is None:
                raise ConfigurationError(
                    f"Seamless database has no result for transformation {checksum}"
                )
            result_checksum = row[0]
            if not isinstance(result_checksum, str):
                raise ConfigurationError(
                    f"Seamless database result is not text for transformation {checksum}"
                )
            records.append(
                format_cache_record(
                    CacheRecord(checksum, result_checksum, pdb_path, psf_path),
                    line_number,
                )
            )
    finally:
        connection.close()
    return "".join(records)


def _atomic_write(path: Path, content: str) -> None:
    """Replace the output only after a complete sibling file has been written."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(content)
        os.replace(temporary, path)
    except OSError as error:
        raise ConfigurationError(f"Cannot write CACHE file {path}: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(cache_file: Path = Path("CACHE")) -> Path:
    """Build ``cache_file`` from debug manifests and the Seamless database."""
    run_dir = Path.cwd()
    rows = _read_manifest(run_dir)
    content = _cache_text(rows, _seamless_database())
    output = cache_file.expanduser()
    _atomic_write(output, content)
    print(f"Wrote {len(rows)} CACHE records to {output}")
    return output


def maincli() -> None:
    """Execute the command-line client."""
    args = ap.parse_args()
    try:
        main(args.cache_file)
    except ConfigurationError as error:
        ap.exit(1, f"build-cache-from-seamless: error: {error}\n")


if __name__ == "__main__":
    sys.exit(maincli())
