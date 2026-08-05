"""Canonical dependency and staging helpers for CNS jobs."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fcntl

from seamless.checksum.calculate_checksum import calculate_checksum, calculate_file_checksum
from seamless_transformer.compression_utils import decompress_bytes, strip_compression_suffix

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


@dataclass(frozen=True)
class CanonicalDependency:
    """One immutable CNS input in its location-independent role."""

    original_path: Path
    canonical_name: str
    checksum: str


@dataclass(frozen=True)
class CanonicalMapping:
    """The complete, immutable identity mapping of a CNS job.

    The mapping is deliberately independent of run roots and generated file
    names.  It is shared by dependency reporting, reference staging and cache
    checksum construction in later phases.
    """

    canonical_script: str
    dependencies: tuple[CanonicalDependency, ...]
    cns_exec: Path
    cns_exec_checksum: str
    output_paths: tuple[Path, ...]
    canonical_output_names: tuple[str, ...]
    output_shape: str
    invariant_dependencies: tuple[str, ...]
    work_dir: Path
    module_dir: Path
    toppar_dir: Path
    unresolved_reads: tuple[str, ...]

    @property
    def checksums(self) -> dict[str, str]:
        """Return the canonical checksum tree used by transformations."""
        return {
            "canonical-cns": self.cns_exec_checksum,
            "canonical.inp": calculate_checksum(self.canonical_script.encode()),
            **{dep.canonical_name: dep.checksum for dep in self.dependencies},
        }

    @property
    def dependency_paths(self) -> dict[Path, str]:
        """Map resolved original paths to their canonical names."""
        return {dependency.original_path: dependency.canonical_name for dependency in self.dependencies}


def compression_transparent_checksum(path: Path) -> str:
    """Checksum a file as Seamless does, transparently to .gz/.zst storage."""
    path = path.resolve()
    _logical_name, suffix = strip_compression_suffix(path.name)
    if suffix is None:
        checksum = calculate_file_checksum(str(path))
        if checksum is None:
            raise OSError(f"Could not checksum {path}")
        return checksum
    return calculate_checksum(decompress_bytes(path.read_bytes(), suffix))


def build_canonical_mapping(
    input_file: Path | str,
    *,
    envvars: dict[str, str],
    cns_exec: Path,
    output_files: Sequence[Path] = (),
    output_pdb_files: Sequence[Path] = (),
    work_dir: Path | None = None,
) -> CanonicalMapping:
    """Resolve every CNS read and rewrite the job into canonical names.

    ``input_file`` may be a materialized script or the in-memory input passed
    to CNS.  ``work_dir`` is captured by :class:`CNSJob` before multiprocessing
    so relative names do not depend on a worker's current directory.
    """
    work_dir = (work_dir or Path.cwd()).resolve()
    module_dir = _resolve_env_path(envvars["MODULE"], work_dir)
    toppar_dir = _resolve_env_path(envvars["TOPPAR"], work_dir)
    if isinstance(input_file, Path):
        script_path = input_file.resolve()
        script = script_path.read_text(encoding="utf-8")
        scan = scan_cns_dependencies(script_path, envvars)
        read_files = [path for path in scan.read_files if path != script_path]
    else:
        script_path = None
        script = input_file
        # Reuse the resolver with a temporary script located in the captured
        # work directory; the temporary path never becomes part of the mapping.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=work_dir, suffix=".inp", delete=False
        ) as handle:
            handle.write(script)
            temporary_input = Path(handle.name)
        try:
            scan = scan_cns_dependencies(temporary_input, envvars)
            read_files = [path for path in scan.read_files if path != temporary_input.resolve()]
        finally:
            temporary_input.unlink(missing_ok=True)

    paths = list(dict.fromkeys(path.resolve() for path in read_files))
    variables = _script_path_variables(script, work_dir, module_dir, toppar_dir)
    canonical_names = _canonical_dependency_names(paths, module_dir, toppar_dir, variables)
    dependencies = tuple(
        CanonicalDependency(path, canonical_names[path], compression_transparent_checksum(path))
        for path in paths
    )
    normalized_outputs = tuple(_absolute_path(path, work_dir) for path in output_files)
    pdb_outputs = {_absolute_path(path, work_dir) for path in output_pdb_files}
    for output in normalized_outputs:
        if output.suffix == ".pdb":
            pdb_outputs.add(output)
    if len(pdb_outputs) != 1 or len(normalized_outputs) not in (1, 2):
        raise ValueError("A cacheable CNS job must declare exactly one PDB and at most one PSF output.")
    pdb_output = next(iter(pdb_outputs))
    psf_outputs = [output for output in normalized_outputs if output.suffix == ".psf"]
    if len(psf_outputs) > 1:
        raise ValueError("A cacheable CNS job may declare at most one PSF output.")
    canonical_output_names = ("canonical-output.pdb",) + (
        ("canonical-output.psf",) if psf_outputs else ()
    )
    output_name_map = {pdb_output: "canonical-output.pdb"}
    if psf_outputs:
        output_name_map[psf_outputs[0]] = "canonical-output.psf"

    canonical_script = _rewrite_canonical_script(
        script,
        work_dir,
        {dependency.original_path: dependency.canonical_name for dependency in dependencies},
        output_name_map,
    )
    _assert_canonical_script(
        canonical_script,
        work_dir,
        [
            *[
                dependency.original_path
                for dependency in dependencies
                if not dependency.canonical_name.startswith(("module/", "toppar/"))
            ],
            *normalized_outputs,
        ],
    )
    cns_exec = cns_exec.resolve()
    return CanonicalMapping(
        canonical_script=canonical_script,
        dependencies=dependencies,
        cns_exec=cns_exec,
        cns_exec_checksum=compression_transparent_checksum(cns_exec),
        output_paths=(pdb_output, *psf_outputs),
        canonical_output_names=canonical_output_names,
        output_shape="pdb+psf" if psf_outputs else "pdb",
        invariant_dependencies=tuple(
            sorted(
                ["canonical-cns"]
                + [
                    dependency.canonical_name
                    for dependency in dependencies
                    if dependency.canonical_name.startswith(("module/", "toppar/"))
                ]
            )
        ),
        work_dir=work_dir,
        module_dir=module_dir,
        toppar_dir=toppar_dir,
        unresolved_reads=tuple(scan.unresolved_reads),
    )


def canonical_mapping_for_job(job) -> CanonicalMapping:
    """Build the canonical mapping for a CNSJob without importing its class."""
    return build_canonical_mapping(
        job.input_file,
        envvars=job.envvars,
        cns_exec=Path(job.cns_exec),
        output_files=job.output_files,
        output_pdb_files=job.output_pdb_files,
        work_dir=job.work_dir,
    )


def _absolute_path(path: Path, work_dir: Path) -> Path:
    return path.resolve() if path.is_absolute() else (work_dir / path).resolve()


def _script_path_variables(
    script: str,
    work_dir: Path,
    module_dir: Path,
    toppar_dir: Path,
) -> dict[Path, str]:
    """Associate assignment variables with resolved paths for named roles."""
    result: dict[Path, str] = {}
    variables: dict[str, str] = {}
    for line in script.splitlines():
        for pattern in _ASSIGNMENT_PATTERNS:
            match = pattern.search(line)
            if match is None:
                continue
            variable, value = match.group("name"), _normalize_assignment_value(match.group("value"))
            variables[variable] = value
            resolved = _resolve_reference(
                token=value,
                current_file=work_dir / "canonical.inp",
                workdir=work_dir,
                module_dir=module_dir,
                toppar_dir=toppar_dir,
                variables=variables,
            )
            if isinstance(resolved, Path) and resolved.exists():
                result[resolved.resolve()] = variable.lower()
            break
    return result


def _canonical_dependency_names(
    paths: Sequence[Path],
    module_dir: Path,
    toppar_dir: Path,
    variables: dict[Path, str],
) -> dict[Path, str]:
    """Assign stable roles in dependency first-reference order."""
    names: dict[Path, str] = {}
    counters = {"pdb": 0, "psf": 0, "generic": 0}
    named_roles = (
        ("unambig", "canonical-unambig.tbl"),
        ("hbond", "canonical-hbond.tbl"),
        ("ambig", "canonical-ambig.tbl"),
        ("dihe", "canonical-dihe.tbl"),
        ("sym", "canonical-symmetry.tbl"),
        ("tensor", "canonical-tensor.tbl"),
        ("ligand_top", "canonical-ligand.top"),
        ("ligand_param", "canonical-ligand.param"),
    )
    for path in paths:
        if path.is_relative_to(module_dir):
            names[path] = f"module/{path.relative_to(module_dir).as_posix()}"
            continue
        if path.is_relative_to(toppar_dir):
            names[path] = f"toppar/{path.relative_to(toppar_dir).as_posix()}"
            continue
        variable = variables.get(path, "")
        named = next((name for marker, name in named_roles if marker in variable), None)
        if named is not None and named not in names.values():
            names[path] = named
            continue
        logical_name, _compression_suffix = strip_compression_suffix(path.name)
        suffix = Path(logical_name).suffix.lower()
        if suffix in (".pdb", ".psf"):
            counters[suffix[1:]] += 1
            names[path] = f"canonical-input-{counters[suffix[1:]]}{suffix}"
        elif suffix == ".tbl" and variable.startswith(("input_aa_", "input_cgtbl_")):
            counters["generic"] += 1
            names[path] = f"canonical-cg-input-{counters['generic']}.tbl"
        else:
            counters["generic"] += 1
            names[path] = f"canonical-input-{counters['generic']}{suffix}"
    return names


def _rewrite_canonical_script(
    script: str,
    work_dir: Path,
    dependency_names: dict[Path, str],
    output_names: dict[Path, str],
) -> str:
    """Replace only resolved job-specific path spellings in CNS text."""
    result = script
    for path, name in {**dependency_names, **output_names}.items():
        candidates = {str(path), path.as_posix(), path.name}
        try:
            candidates.add(str(path.relative_to(work_dir)))
        except ValueError:
            pass
        for candidate in sorted(candidates, key=len, reverse=True):
            if candidate:
                result = result.replace(candidate, name)
    return result


def _assert_canonical_script(script: str, work_dir: Path, paths: Sequence[Path]) -> None:
    """Reject location-dependent canonical scripts before a cache can use them."""
    leaked = [str(work_dir)]
    leaked.extend(path.name for path in paths if path.name)
    for token in leaked:
        if token and token in script:
            raise ValueError(f"Canonical CNS script leaked {token!r} from job in {work_dir}")
    match = re.search(r"(?:^|/)\d+_[A-Za-z][A-Za-z0-9_]*", script)
    if match:
        raise ValueError(f"Canonical CNS script leaked step-folder token {match.group(0)!r}")


def write_cns_dependencies(step_dir: Path, mapping: CanonicalMapping) -> None:
    """Atomically union this job's invariant dependencies into a step manifest."""
    step_dir = step_dir.resolve()
    lock_path = step_dir / ".cns-dependencies.lock"
    manifest_path = step_dir / "CNS_DEPENDENCIES"
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            current = (
                set(manifest_path.read_text(encoding="utf-8").splitlines())
                if manifest_path.exists()
                else set()
            )
            current.update(mapping.invariant_dependencies)
            temporary = step_dir / f".CNS_DEPENDENCIES.{os.getpid()}.tmp"
            temporary.write_text("\n".join(sorted(current)) + "\n", encoding="utf-8")
            os.replace(temporary, manifest_path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
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
