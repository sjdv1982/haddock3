"""Canonical dependency and staging helpers for CNS jobs."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from haddock.core.typing import Optional, Sequence, Union


_ASSIGNMENT_PATTERNS = (
    re.compile(
        r'eval(?:uate)?\s*\(\s*\$(?P<name>[A-Za-z0-9_]+)\s*=\s*(?P<value>.*?)\s*\)'
    ),
    re.compile(
        r'\{===>\}\s*(?P<name>[A-Za-z0-9_]+)\s*=\s*(?P<value>.*?)\s*;'
    ),
)

_REFERENCE_PATTERN = re.compile(
    r'@@?(?P<target>MODULE:[^\s;]+|TOPPAR:[^\s;]+|[$&][A-Za-z0-9_]+|[^\s;]+)'
)
_DYNAMIC_TOPPAR_PREFIX_PATTERN = re.compile(
    r'"?(?P<prefix>TOPPAR:[^"\s]+?)"?\s*\+\s*encode\('
)
@dataclass
class CNSDependencyScan:
    """Resolved CNS read dependencies for one job."""

    read_files: list[Path]
    unresolved_reads: list[str]


@dataclass
class StagedCNSJob:
    """Canonical staged CNS job layout for a reference invocation."""

    stage_dir: Path
    run_root: Path
    module_dir: Path
    toppar_dir: Path
    cns_exec: Path
    staged_job_dir: Path
    staged_input_file: Path
    staged_cns_exec: Path
    staged_module_dir: Path
    staged_toppar_dir: Path

    def staged_path(self, path: Path) -> Path:
        """Return the canonical staged path for an original path."""
        return _canonical_stage_path(
            path,
            stage_dir=self.stage_dir,
            run_root=self.run_root,
            module_dir=self.module_dir,
            toppar_dir=self.toppar_dir,
            cns_exec=self.cns_exec,
        )


class _IgnoredReference:
    """Sentinel for optional references that do not resolve to a file."""


IGNORED_REFERENCE = _IgnoredReference()


def scan_cns_dependencies(input_file: Path, envvars: dict[str, str]) -> CNSDependencyScan:
    """Resolve explicit CNS read dependencies for Seamless execution."""
    input_file = input_file.resolve()
    workdir = input_file.parent
    module_dir = _resolve_env_path(envvars["MODULE"], workdir)
    toppar_dir = _resolve_env_path(envvars["TOPPAR"], workdir)

    read_files: set[Path] = set()
    unresolved_reads: list[str] = []
    visited: set[Path] = set()

    def scan_file(path: Path, variables: dict[str, str]) -> None:
        path = path.resolve()
        if path in visited:
            return
        visited.add(path)

        if not path.exists():
            unresolved_reads.append(str(path))
            return

        read_files.add(path)
        local_vars = dict(variables)

        guard_stack: list[tuple[str, bool]] = []

        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.split("!", 1)[0].strip()
            if not line:
                continue

            lowered = line.lower()
            if lowered.startswith("if ("):
                guard_var = _extract_nonempty_guard_variable(line)
                if guard_var is not None:
                    guard_stack.append((guard_var, True))
            elif lowered.startswith("end if") and guard_stack:
                guard_stack.pop()

            for pattern in _ASSIGNMENT_PATTERNS:
                match = pattern.search(line)
                if match:
                    value = _normalize_assignment_value(match.group("value"))
                    local_vars[match.group("name")] = value
                    dynamic_prefix = _extract_dynamic_toppar_prefix(value)
                    if dynamic_prefix is not None:
                        local_vars[f"__dynamic_prefix__{match.group('name')}"] = dynamic_prefix
                    break

            for match in _REFERENCE_PATTERN.finditer(line):
                token = match.group("target")
                resolved = _resolve_reference(
                    token=token,
                    current_file=path,
                    workdir=workdir,
                    module_dir=module_dir,
                    toppar_dir=toppar_dir,
                    variables=local_vars,
                )
                if resolved is IGNORED_REFERENCE:
                    continue
                if resolved is None:
                    if _is_guarded_optional_reference(token, guard_stack):
                        continue
                    unresolved_reads.append(token)
                    continue
                if isinstance(resolved, list):
                    for resolved_path in resolved:
                        if not resolved_path.exists():
                            unresolved_reads.append(str(resolved_path))
                            continue
                        read_files.add(resolved_path)
                        if resolved_path.suffix.lower() == ".cns":
                            scan_file(resolved_path, local_vars)
                    continue
                if not isinstance(resolved, Path):
                    continue
                if not resolved.exists():
                    unresolved_reads.append(str(resolved))
                    continue

                read_files.add(resolved)
                if resolved.suffix.lower() == ".cns":
                    scan_file(resolved, local_vars)

    scan_file(input_file, {})
    return CNSDependencyScan(
        read_files=sorted(read_files),
        unresolved_reads=sorted(set(unresolved_reads)),
    )


def stage_cns_job(
    *,
    input_file: Path,
    envvars: dict[str, str],
    cns_exec: Path,
    read_files: Sequence[Path],
) -> StagedCNSJob:
    """Stage a CNS job in a canonical tree with stable workspace paths."""
    input_file = input_file.resolve()
    cns_exec = cns_exec.resolve()
    job_dir = input_file.parent
    run_root = job_dir.parent
    module_dir = _resolve_env_path(envvars["MODULE"], job_dir)
    toppar_dir = _resolve_env_path(envvars["TOPPAR"], job_dir)
    stage_dir = Path(tempfile.mkdtemp(prefix="haddock-seamless-"))

    staged_input_file = _copy_into_stage(
        input_file,
        stage_dir=stage_dir,
        run_root=run_root,
        module_dir=module_dir,
        toppar_dir=toppar_dir,
        cns_exec=cns_exec,
    )
    staged_cns_exec = _copy_into_stage(
        cns_exec,
        stage_dir=stage_dir,
        run_root=run_root,
        module_dir=module_dir,
        toppar_dir=toppar_dir,
        cns_exec=cns_exec,
    )
    for dependency in read_files:
        _copy_into_stage(
            dependency,
            stage_dir=stage_dir,
            run_root=run_root,
            module_dir=module_dir,
            toppar_dir=toppar_dir,
            cns_exec=cns_exec,
        )

    staged_job_dir = stage_dir / "run" / job_dir.relative_to(run_root)
    staged_module_dir = stage_dir / "module"
    staged_toppar_dir = stage_dir / "toppar"
    return StagedCNSJob(
        stage_dir=stage_dir,
        run_root=run_root,
        module_dir=module_dir,
        toppar_dir=toppar_dir,
        cns_exec=cns_exec,
        staged_job_dir=staged_job_dir,
        staged_input_file=staged_input_file,
        staged_cns_exec=staged_cns_exec,
        staged_module_dir=staged_module_dir,
        staged_toppar_dir=staged_toppar_dir,
    )


def make_seamless_wrapper(stage_dir: Path) -> Path:
    """Create the tiny wrapper script used by seamless-run."""
    wrapper = stage_dir / "run-cns.sh"
    wrapper.write_text(
        "#!/bin/sh\n"
        "set -u\n"
        "cd \"$JOB_DIR\"\n"
        "export MODDIR=.\n"
        "export MODULE=\"$MODULE_DIR\"\n"
        "export TOPPAR=\"$TOPPAR_DIR\"\n"
        "chmod +x \"$CNS_EXEC\"\n"
        "\"$CNS_EXEC\" < \"$INPUT_FILE\" > \"$STDOUT_FILE\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def _copy_into_stage(
    source: Path,
    *,
    stage_dir: Path,
    run_root: Path,
    module_dir: Path,
    toppar_dir: Path,
    cns_exec: Path,
) -> Path:
    destination = _canonical_stage_path(
        source,
        stage_dir=stage_dir,
        run_root=run_root,
        module_dir=module_dir,
        toppar_dir=toppar_dir,
        cns_exec=cns_exec,
    )
    source = source.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if os.access(source, os.X_OK):
        destination.chmod(destination.stat().st_mode | 0o111)
    return destination


def _canonical_stage_path(
    source: Path,
    *,
    stage_dir: Path,
    run_root: Path,
    module_dir: Path,
    toppar_dir: Path,
    cns_exec: Path,
) -> Path:
    """Map an original path to a stable path inside the staged workspace."""
    source = source.resolve()
    if source == cns_exec.resolve():
        return stage_dir / "bin" / cns_exec.name
    if source.is_relative_to(run_root):
        return stage_dir / "run" / source.relative_to(run_root)
    if source.is_relative_to(module_dir):
        return stage_dir / "module" / source.relative_to(module_dir)
    if source.is_relative_to(toppar_dir):
        return stage_dir / "toppar" / source.relative_to(toppar_dir)
    return stage_dir / "external" / source.as_posix().lstrip(os.sep)


def _resolve_env_path(raw_path: str, workdir: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path.resolve()
    return (workdir / path).resolve()


def _resolve_reference(
    *,
    token: str,
    current_file: Path,
    workdir: Path,
    module_dir: Path,
    toppar_dir: Path,
    variables: dict[str, str],
    seen_variables: Optional[set[str]] = None,
) -> Optional[Union[Path, list[Path], _IgnoredReference]]:
    seen_variables = seen_variables or set()
    resolved_from_variable = False
    if token.startswith(("$", "&")):
        variable_name = token[1:]
        if variable_name in seen_variables:
            return None
        seen_variables.add(variable_name)
        dynamic_prefix = variables.get(f"__dynamic_prefix__{variable_name}")
        if variable_name not in variables:
            if dynamic_prefix is not None:
                prefix_path = _resolve_reference(
                    token=dynamic_prefix,
                    current_file=current_file,
                    workdir=workdir,
                    module_dir=module_dir,
                    toppar_dir=toppar_dir,
                    variables=variables,
                    seen_variables=seen_variables,
                )
                if isinstance(prefix_path, Path):
                    return sorted(prefix_path.parent.glob(f"{prefix_path.name}*"))
            return None
        token = variables[variable_name]
        if token == "":
            return IGNORED_REFERENCE
        resolved_from_variable = True
        if token.startswith(("$", "&")):
            return _resolve_reference(
                token=token,
                current_file=current_file,
                workdir=workdir,
                module_dir=module_dir,
                toppar_dir=toppar_dir,
                variables=variables,
                seen_variables=seen_variables,
            )
        if dynamic_prefix is not None and any(fragment in token for fragment in ("+", "encode(")):
            prefix_path = _resolve_reference(
                token=dynamic_prefix,
                current_file=current_file,
                workdir=workdir,
                module_dir=module_dir,
                toppar_dir=toppar_dir,
                variables=variables,
                seen_variables=seen_variables,
            )
            if isinstance(prefix_path, Path):
                return sorted(prefix_path.parent.glob(f"{prefix_path.name}*"))

    if not token or any(fragment in token for fragment in ("+", "encode(", "$", "&")):
        return None

    if token.startswith("MODULE:"):
        return (module_dir / token.split(":", 1)[1]).resolve()

    if token.startswith("TOPPAR:"):
        return (toppar_dir / token.split(":", 1)[1]).resolve()

    candidate = Path(token)
    if candidate.is_absolute():
        return candidate.resolve()
    relative_base = workdir if resolved_from_variable else current_file.parent
    return (relative_base / candidate).resolve()


def _extract_nonempty_guard_variable(line: str) -> Optional[str]:
    match = re.search(r'\$([A-Za-z0-9_]+)\s*#\s*""', line)
    if match is not None:
        return match.group(1)

    match = re.search(r'&BLANK%([A-Za-z0-9_]+)\s*=\s*false', line, re.IGNORECASE)
    if match is not None:
        return match.group(1)

    return None


def _is_guarded_optional_reference(
    token: str,
    guard_stack: list[tuple[str, bool]],
) -> bool:
    if not token.startswith(("$", "&")):
        return False
    variable_name = token[1:]
    return any(guard_var == variable_name and is_optional for guard_var, is_optional in guard_stack)


def _extract_dynamic_toppar_prefix(value: str) -> Optional[str]:
    match = _DYNAMIC_TOPPAR_PREFIX_PATTERN.search(value)
    if match is None:
        return None
    return match.group("prefix")


def _normalize_assignment_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value
