#!/usr/bin/env python3
"""Report parameter cleanup removals that keep non-identical reverse records.

This helper compares CNS parameter files between a base ref and a target ref.
For each removed BOND/ANGLe/DIHEdral record, it looks for surviving reverse-order
records with the same atom tuple but different parameters.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PARAM_RE = re.compile(r"(^|\s)(BOND|ANGLe|DIHEdral)\s+", re.IGNORECASE)
ARITY = {
    "BOND": 2,
    "ANGLE": 3,
    "DIHEDRAL": 4,
}


def find_repo_root() -> Path:
    """Return the repository root for the current checkout."""
    root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.PIPE
    ).strip()
    return Path(root)


REPO_ROOT = find_repo_root()


@dataclass(frozen=True)
class ParamRecord:
    """One parsed CNS parameter record."""

    path: str
    line: int
    kind: str
    atoms: tuple[str, ...]
    params: tuple[str, ...]
    continuations: tuple[tuple[str, ...], ...]

    @property
    def reversed_atoms(self) -> tuple[str, ...]:
        """Return the reverse-order atom tuple."""
        return tuple(reversed(self.atoms))

    @property
    def canonical_atoms(self) -> tuple[str, ...]:
        """Return an orientation-independent atom tuple."""
        return min(self.atoms, self.reversed_atoms)

    @property
    def value(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        """Return parameters plus continuation lines for equality checks."""
        return self.params, self.continuations

    @property
    def identity(
        self,
    ) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[tuple[str, ...], ...]]:
        """Return the semantic record identity, ignoring atom order."""
        return self.kind, self.canonical_atoms, self.params, self.continuations

    @property
    def atom_key(self) -> tuple[str, tuple[str, ...]]:
        """Return the semantic atom key, ignoring atom order."""
        return self.kind, self.canonical_atoms


def run_git(args: list[str]) -> str:
    """Run a git command and return stdout."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), *args], text=True, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as err:
        sys.stderr.write(err.stderr)
        raise


def normalize_kind(kind: str) -> str:
    """Normalize CNS spelling to parser keys."""
    upper = kind.upper()
    if upper == "ANGLE":
        return "ANGLE"
    if upper == "DIHEDRAL":
        return "DIHEDRAL"
    return "BOND"


def parse_record_line(line: str) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    """Parse a CNS parameter declaration line."""
    match = PARAM_RE.search(line)
    if not match:
        return None

    kind = normalize_kind(match.group(2))
    fields = line[match.end() :].split()
    atom_count = ARITY[kind]
    if len(fields) < atom_count:
        return None

    atoms = tuple(fields[:atom_count])
    params = tuple(fields[atom_count:])
    return kind, atoms, params


def parse_param_records(path: str, text: str) -> list[ParamRecord]:
    """Parse BOND/ANGLe/DIHEdral records from a CNS parameter file."""
    records: list[ParamRecord] = []
    current: dict[str, object] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        records.append(
            ParamRecord(
                path=path,
                line=current["line"],  # type: ignore[arg-type]
                kind=current["kind"],  # type: ignore[arg-type]
                atoms=current["atoms"],  # type: ignore[arg-type]
                params=current["params"],  # type: ignore[arg-type]
                continuations=tuple(current["continuations"]),  # type: ignore[arg-type]
            )
        )
        current = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        parsed = parse_record_line(stripped)
        if parsed:
            flush()
            kind, atoms, params = parsed
            current = {
                "line": line_number,
                "kind": kind,
                "atoms": atoms,
                "params": params,
                "continuations": [],
            }
            continue

        if current is None:
            continue

        if not stripped or stripped.startswith(("!", "{")):
            flush()
            continue

        if PARAM_RE.search(stripped):
            flush()
            continue

        current["continuations"].append(tuple(stripped.split()))  # type: ignore[index, union-attr]

    flush()
    return records


def git_show(ref: str, path: str) -> str:
    """Return a file at a given git ref."""
    return run_git(["show", f"{ref}:{path}"])


def default_base(target: str) -> str:
    """Find a reasonable default base for the current branch."""
    for candidate in ("upstream/main", "origin/main", "main"):
        try:
            return run_git(["merge-base", candidate, target]).strip()
        except subprocess.CalledProcessError:
            continue
    sys.exit("Could not infer a base ref. Pass --base explicitly.")


def default_files(base: str, target: str) -> list[str]:
    """Return changed CNS parameter files between base and target."""
    diff_names = run_git(
        ["diff", "--name-only", base, target, "--", ":(glob)**/*.param"]
    )
    return [line for line in diff_names.splitlines() if line]


def to_repo_path(path: str) -> str:
    """Return a repository-relative path for CLI file arguments."""
    given = Path(path)
    if given.is_absolute():
        try:
            return given.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            return path

    cwd_path = (Path.cwd() / given).resolve()
    try:
        return cwd_path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path


def removed_records(
    base_records: Iterable[ParamRecord], target_records: Iterable[ParamRecord]
) -> list[ParamRecord]:
    """Return base records whose exact semantic identity no longer survives."""
    target_counts = Counter(record.identity for record in target_records)
    removed: list[ParamRecord] = []

    for record in base_records:
        if target_counts[record.identity] > 0:
            target_counts[record.identity] -= 1
        else:
            removed.append(record)

    return removed


def format_value(record: ParamRecord) -> str:
    """Format record parameters, including continuation values."""
    lines = [" ".join(record.params)]
    lines.extend(" ".join(line) for line in record.continuations)
    return "\n      ".join(lines)


def report_file(path: str, base: str, target: str) -> int:
    """Report suspicious removals for one parameter file."""
    base_records = parse_param_records(path, git_show(base, path))
    target_records = parse_param_records(path, git_show(target, path))

    target_by_atom_key: dict[tuple[str, tuple[str, ...]], list[ParamRecord]] = (
        defaultdict(list)
    )
    for record in target_records:
        target_by_atom_key[record.atom_key].append(record)

    findings = 0
    for old in removed_records(base_records, target_records):
        reverse_records = [
            new
            for new in target_by_atom_key.get(old.atom_key, [])
            if new.atoms == old.reversed_atoms and new.value != old.value
        ]
        if not reverse_records:
            continue

        findings += 1
        print(f"{old.path}:{old.line}: removed {old.kind} {' '.join(old.atoms)}")
        print("  old parameters:")
        print(f"      {format_value(old)}")
        print("  surviving reverse-order entries with different parameters:")
        for new in reverse_records:
            print(f"    {new.path}:{new.line}: {new.kind} {' '.join(new.atoms)}")
            print("      new parameters:")
            print(f"        {format_value(new)}")
        print()

    return findings


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Find removed CNS parameter records whose surviving reverse-order "
            "entries have different parameters."
        )
    )
    parser.add_argument(
        "--base",
        help="Base git ref. Defaults to merge-base(upstream/main, --target).",
    )
    parser.add_argument(
        "--target",
        default="HEAD",
        help="Target git ref to compare against. Defaults to HEAD.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Parameter files to inspect. Defaults to changed *.param files.",
    )
    return parser


def main() -> int:
    """Run the checker."""
    args = build_arg_parser().parse_args()
    target = args.target
    base = args.base or default_base(target)
    files = [to_repo_path(path) for path in args.files] or default_files(base, target)

    if not files:
        print(f"No changed .param files between {base} and {target}.")
        return 0

    total = 0
    for path in files:
        if Path(path).suffix != ".param":
            sys.stderr.write(f"Skipping non-.param file: {path}\n")
            continue
        total += report_file(path, base, target)

    if total:
        print(
            f"Found {total} removed records with non-identical reverse-order survivors."
        )
        return 1

    print("No removed records with non-identical reverse-order survivors found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
