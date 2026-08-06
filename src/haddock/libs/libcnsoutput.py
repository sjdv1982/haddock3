"""Helpers for normalizing CNS-generated output artifacts."""

import os
import uuid
from pathlib import Path

from seamless_transformer.compression_utils import decompress_bytes, strip_compression_suffix

from haddock.core.typing import FilePath


CNS_PDB_VOLATILE_PREFIXES = (
    "REMARK FILENAME=",
    "REMARK initial structure ",
    "REMARK DATE:",
)


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

    # A restored cache artifact can be a hardlink to the source run.  Replace
    # the destination atomically instead of writing it in place, otherwise a
    # normalization here would silently mutate the source cache as well.
    temporary = pdb_path.with_name(f".{pdb_path.name}.normalize-{uuid.uuid4().hex}")
    try:
        temporary.write_bytes(stable)
        os.replace(temporary, pdb_path)
    finally:
        temporary.unlink(missing_ok=True)
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
    pdb_path = Path(path)
    _logical_name, suffix = strip_compression_suffix(pdb_path.name)
    pdb_bytes = pdb_path.read_bytes()
    if suffix is not None:
        pdb_bytes = decompress_bytes(pdb_bytes, suffix)
    return normalize_cns_pdb_bytes(pdb_bytes) == pdb_bytes
