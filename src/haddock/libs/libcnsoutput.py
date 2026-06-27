"""Helpers for normalizing CNS-generated output artifacts."""

from pathlib import Path

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

    original = pdb_path.read_text(encoding="utf-8")
    stable_lines = [
        line
        for line in original.splitlines()
        if not line.startswith(CNS_PDB_VOLATILE_PREFIXES)
    ]
    stable = "\n".join(stable_lines)
    if stable_lines:
        stable += "\n"

    if stable == original:
        return False

    pdb_path.write_text(stable, encoding="utf-8")
    return True
