"""Derived-by-damage fixtures.

A base run is copied -- **content copy, fresh inodes** -- and then damaged from
the outside: an output deleted, truncated, modified in place, replaced by a
same-size file, replaced by a directory or a symlink, made unreadable,
compressed, or relocated to another filesystem.  No CNS runs, so these are the
cheapest high-value fixtures in the corpus, and they cover most of Axis 12 and
part of Axes 1, 9 and 11.

Every outcome here is MUST-DEGRADE or MUST-HIT: the artifact store maps
``result checksum -> bytes``, verified on read, so a bad locator can only fail
to find bytes, never yield wrong ones.  There is no third case.

These functions *write* a run's ``CACHE`` file in two places (``drop_records``,
``corrupt_records``), because Axis 11.11-11.13 are about malformed records.
That is fixture construction, not observation: no assertion in the suite ever
reads ``CACHE``.
"""

from __future__ import annotations

import gzip
import os
import shutil
import stat
from pathlib import Path

from .harness import cacheable_artifacts


def copy_run(source: Path, destination: Path) -> Path:
    """Content copy with fresh inodes, so Gate 1 cannot be fooled by the copy."""
    if destination.exists():
        shutil.rmtree(destination, onexc=_force_remove)
    shutil.copytree(source, destination, symlinks=True)
    for entry in destination.rglob("*"):
        try:
            os.chmod(entry, entry.stat().st_mode | stat.S_IWUSR)
        except OSError:
            continue
    return destination


def _force_remove(function, path, _excinfo):  # pragma: no cover - cleanup path
    os.chmod(path, 0o700)
    function(path)


def artifacts(run_dir: Path) -> list[Path]:
    """Absolute paths of every cacheable output in a run."""
    return [run_dir / artifact.relative for artifact in cacheable_artifacts(run_dir)]


def pick(run_dir: Path, module: str, index: int = 0) -> Path:
    """The ``index``-th ``.pdb`` output of ``module``, for a targeted lesion."""
    candidates = [
        run_dir / artifact.relative
        for artifact in cacheable_artifacts(run_dir)
        if artifact.module == module and artifact.kind == "pdb"
    ]
    return sorted(candidates)[index]


# -- 12.1 artifact deleted ------------------------------------------------


def delete(path: Path) -> None:
    path.unlink()


# -- 12.2 artifact modified in place --------------------------------------


def modify_in_place(path: Path) -> None:
    """Change one coordinate. Same length, so the size check cannot catch it."""
    data = bytearray(path.read_bytes())
    for index, byte in enumerate(data):
        if byte in b"0123456789" and index > 4096:
            data[index] = ord("9") if byte != ord("9") else ord("0")
            break
    path.write_bytes(bytes(data))


# -- 12.3 artifact truncated ----------------------------------------------


def truncate(path: Path) -> None:
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])


# -- 12.4 artifact replaced by a same-size file ---------------------------


def replace_same_size(path: Path) -> None:
    path.write_bytes(b"X" * path.stat().st_size)


# -- 12.5 artifact replaced by a directory or a symlink -------------------


def replace_with_directory(path: Path) -> None:
    path.unlink()
    path.mkdir()
    (path / "not-a-pdb").write_text("", encoding="utf-8")


def replace_with_symlink(path: Path, target: Path) -> None:
    path.unlink()
    path.symlink_to(target)


def replace_with_dangling_symlink(path: Path) -> None:
    path.unlink()
    path.symlink_to(path.parent / "nowhere-at-all.pdb")


# -- 12.6 artifact unreadable ---------------------------------------------


def make_unreadable(path: Path) -> None:
    os.chmod(path, 0o000)


# -- 12.7 / 1.7 artifact compressed ---------------------------------------


def compress(path: Path) -> Path:
    """Gzip an artifact in place, as ``clean``/``postprocess`` would."""
    compressed = Path(f"{path}.gz")
    with open(path, "rb") as plain, gzip.open(compressed, "wb") as archive:
        shutil.copyfileobj(plain, archive)
    path.unlink()
    return compressed


def compress_run(run_dir: Path) -> None:
    """Compress every cacheable output of a run."""
    for path in artifacts(run_dir):
        if path.exists():
            compress(path)


# -- 11.9 / 12.11 records without bytes -----------------------------------


def strip_artifacts(run_dir: Path) -> None:
    """Delete every cacheable output but keep the records.

    A valid and useful state: the names of the answers are known, their
    content is not.  It must not be treated as corruption.
    """
    for path in artifacts(run_dir):
        if path.exists():
            path.unlink()


# -- 11.2 disjoint coverage / 11.11-11.13 malformed records ---------------


def drop_records(run_dir: Path, module: str) -> int:
    """Remove records whose artifact path lies in a step of ``module``.

    Used to manufacture *disjoint* cache coverage from two runs of the same
    workflow, which is otherwise impossible to construct.
    """
    cache = run_dir / "CACHE"
    kept, dropped = [], 0
    for line in cache.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        step = fields[2].split("/")[0] if len(fields) > 2 else ""
        if step.partition("_")[2] == module:
            dropped += 1
            continue
        kept.append(line)
    cache.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")
    for path in artifacts(run_dir):
        if path.parent.name.partition("_")[2] == module and path.exists():
            path.unlink()
    return dropped


def corrupt_records(run_dir: Path, how: str) -> None:
    """Make ``CACHE`` malformed in a specific, named way (Axis 11.11-11.13)."""
    cache = run_dir / "CACHE"
    text = cache.read_text(encoding="utf-8")
    lines = text.splitlines()
    if how == "truncated":
        cache.write_text("\n".join(lines[:-1]) + "\n" + lines[-1][:20], encoding="utf-8")
    elif how == "blank-line":
        cache.write_text("\n".join(lines[:1] + [""] + lines[1:]) + "\n", encoding="utf-8")
    elif how == "wrong-arity":
        broken = "\t".join(lines[0].split("\t")[:2])
        cache.write_text("\n".join([broken] + lines[1:]) + "\n", encoding="utf-8")
    elif how == "non-checksum-key":
        fields = lines[0].split("\t")
        fields[0] = "not-a-checksum"
        cache.write_text(
            "\n".join(["\t".join(fields)] + lines[1:]) + "\n", encoding="utf-8"
        )
    elif how == "duplicate-agreeing":
        cache.write_text(text + lines[0] + "\n", encoding="utf-8")
    elif how == "duplicate-conflicting":
        fields = lines[0].split("\t")
        fields[1] = ("0" * 63 + "1") if fields[1] != "0" * 63 + "1" else "0" * 64
        cache.write_text(text + "\t".join(fields) + "\n", encoding="utf-8")
    else:  # pragma: no cover - programming error
        raise ValueError(f"unknown corruption {how!r}")


def poison_store(run_dir: Path) -> None:
    """Keep every record but replace every artifact's bytes (Axis 12.10)."""
    for path in artifacts(run_dir):
        if path.exists():
            replace_same_size(path)
