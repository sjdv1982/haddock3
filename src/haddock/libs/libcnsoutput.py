"""Helpers for normalizing CNS-generated output artifacts."""

import os
import uuid
from pathlib import Path

from seamless_transformer.compression_utils import decompress_bytes, strip_compression_suffix

from haddock.core.typing import FilePath, Optional


CNS_PDB_VOLATILE_PREFIXES = (
    "REMARK FILENAME=",
    "REMARK initial structure ",
    "REMARK DATE:",
)


def _is_volatile_psf_line(line: str) -> bool:
    """Whether a PSF line is the wall-clock stamp CNS writes into its title.

    CNS emits ``  DATE:31-Aug-2026  01:17:08       created by user: unknown``
    into the free-text title block of every PSF it writes, so two runs of the
    same topology differ in that one line and in nothing else.  Both markers
    are required, so that structural data can never be mistaken for the stamp.
    """
    stripped = line.strip()
    return stripped.startswith("DATE:") and "created by user:" in stripped


def _rewrite_atomically(path: Path, stable: bytes) -> None:
    """Replace a file's bytes without ever writing into it in place.

    A restored cache artifact can be a hardlink to the source run, so an
    in-place write here would silently mutate the source as well.
    """
    temporary = path.with_name(f".{path.name}.normalize-{uuid.uuid4().hex}")
    try:
        temporary.write_bytes(stable)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def normalize_cns_pdb(path: FilePath) -> bool:
    """Remove run-volatile CNS header lines from a PDB file.

    Returns ``True`` when the file bytes were changed.
    """
    pdb_path = Path(path)
    if not pdb_path.exists():
        return False

    original = pdb_path.read_bytes()
    stable = normalize_cns_pdb_bytes(original)

    if stable == original:
        return False

    _rewrite_atomically(pdb_path, stable)
    return True


def normalize_cns_pdb_bytes(pdb_bytes: bytes) -> bytes:
    """Return PDB bytes without CNS run-volatile header lines."""
    original = pdb_bytes.decode("utf-8")
    stable_lines = [
        line
        for line in original.splitlines()
        if not line.startswith(CNS_PDB_VOLATILE_PREFIXES)
    ]
    stable = "\n".join(stable_lines)
    if stable_lines:
        stable += "\n"
    return stable.encode("utf-8")


def is_normalized_cns_pdb(path: FilePath) -> bool:
    """Return whether a PDB, compressed or not, has stable CNS headers."""
    return _is_normalized(path, normalize_cns_pdb_bytes)


def _is_normalized(path: FilePath, normalize) -> bool:
    """Whether a file, compressed or not, is unchanged by ``normalize``."""
    artifact = Path(path)
    _logical_name, suffix = strip_compression_suffix(artifact.name)
    artifact_bytes = artifact.read_bytes()
    if suffix is not None:
        artifact_bytes = decompress_bytes(artifact_bytes, suffix)
    return normalize(artifact_bytes) == artifact_bytes


def normalize_cns_psf(path: FilePath) -> bool:
    """Remove the run-volatile CNS date stamp from a PSF file.

    Returns ``True`` when the file bytes were changed.

    Without this, two runs of the same topology produce byte-identical PDBs
    and PSFs that differ in their embedded date.  Every downstream CNS job
    reads the PSF, so that one line makes a topology non-reproducible and
    everything computed from it unshareable between runs.
    """
    psf_path = Path(path)
    if not psf_path.exists():
        return False

    original = psf_path.read_bytes()
    stable = normalize_cns_psf_bytes(original)

    if stable == original:
        return False

    _rewrite_atomically(psf_path, stable)
    return True


def normalize_cns_psf_bytes(psf_bytes: bytes) -> bytes:
    """Return PSF bytes without the CNS date stamp.

    The stamp sits inside the free-text title block, which is delimited by
    lines of its own, so dropping one line from it leaves a well-formed file.
    """
    original = psf_bytes.decode("utf-8")
    stable_lines = [
        line for line in original.splitlines() if not _is_volatile_psf_line(line)
    ]
    stable = "\n".join(stable_lines)
    if stable_lines:
        stable += "\n"
    return stable.encode("utf-8")


def is_normalized_cns_psf(path: FilePath) -> bool:
    """Return whether a PSF, compressed or not, has a stable CNS title."""
    return _is_normalized(path, normalize_cns_psf_bytes)


def is_normalized_cns_artifact(
    path: FilePath, logical_name: Optional[str] = None
) -> bool:
    """Return whether a CNS output artifact is free of run-volatile content.

    Dispatches on the suffix, so a caller does not have to know which
    normalization applies; anything not normalized here is reported as
    already stable.

    ``logical_name`` names the artifact when ``path`` does not.  A cache
    stages an artifact under a temporary name of its own choosing, and
    dispatching on *that* would silently classify every staged file as
    "nothing to check".
    """
    name = logical_name if logical_name is not None else Path(path).name
    stripped, _compression = strip_compression_suffix(Path(name).name)
    suffix = Path(stripped).suffix.lower()
    if suffix == ".pdb":
        return is_normalized_cns_pdb(path)
    if suffix == ".psf":
        return is_normalized_cns_psf(path)
    return True
