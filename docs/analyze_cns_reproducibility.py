"""Analyze reproducibility of the Phase 1 CNS rigidbody witness job.

This developer tool intentionally reuses the Phase 1 integration-test harness.
It runs the same generated CNS A-job several times in fresh directories and
compares the generated CNS input, PDB, stdout, and optional error/seed files at
raw and lightly normalized checksum levels.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from haddock.core.defaults import cns_exec
from haddock.gear.expandable_parameters import populate_mol_parameters_in_module
from haddock.libs.libio import working_directory
from haddock.libs.libsubprocess import CNSJob
from haddock.modules.sampling.rigidbody import (
    DEFAULT_CONFIG as DEFAULT_RIGIDBODY_CONFIG,
)
from haddock.modules.sampling.rigidbody import HaddockModule as RigidbodyModule

from integration_tests import GOLDEN_DATA
from integration_tests.test_witness_rigidbody import (
    PreparedRigidBodyIO,
    WITNESS_DATA,
    run_rigidbody_ajob,
)
from integration_tests.witness_helpers import (
    bytes_sha256,
    extract_haddock_model_witnesses,
    normalize_cns_pdb_for_checksum,
)


ARTIFACTS = (
    "rigidbody_1.inp",
    "rigidbody_1.pdb",
    "rigidbody_1.out",
    "rigidbody_1.cnserr",
    "rigidbody_1.seed",
)
CASES = ("cmrest", "air_random_removal")
AIR_RESTRAINTS = (
    REPO_ROOT
    / "examples"
    / "docking-protein-protein"
    / "data"
    / "e2a-hpr_air.tbl"
)


def main() -> None:
    """Run the reproducibility analysis and print JSON."""
    parser = argparse.ArgumentParser(
        description="Analyze Phase 1 CNS rigidbody artifact reproducibility.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of fresh A-job runs to compare.",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="Directory where run_*/ folders are created. Defaults to /tmp.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete the temporary work root after printing the JSON summary.",
    )
    parser.add_argument(
        "--case",
        choices=(*CASES, "all"),
        default="all",
        help="Fixture case to run.",
    )
    args = parser.parse_args()

    if args.runs < 2:
        raise SystemExit("--runs must be at least 2")

    if args.work_root is None:
        work_root = Path(tempfile.mkdtemp(prefix="haddock3-cns-repro-"))
        created_temp_root = True
    else:
        work_root = args.work_root.resolve()
        work_root.mkdir(parents=True, exist_ok=True)
        created_temp_root = False

    try:
        selected_cases = CASES if args.case == "all" else (args.case,)
        result = {
            "runs": args.runs,
            "work_root": str(work_root),
            "cases": {
                case: analyze_case(work_root / case, args.runs, case)
                for case in selected_cases
            },
        }
        result["work_root_created_by_tool"] = created_temp_root
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        if args.cleanup:
            shutil.rmtree(work_root)


def analyze_case(work_root: Path, runs: int, case: str) -> dict[str, Any]:
    """Run one Phase 1 A-job case repeatedly and compare artifacts."""
    work_root.mkdir(parents=True, exist_ok=True)
    run_dirs = []
    modules = []
    for idx in range(1, runs + 1):
        run_dir = work_root / f"run_{idx}"
        run_dir.mkdir(parents=True, exist_ok=False)
        module, _job = run_case(case, run_dir)
        run_dirs.append(run_dir)
        modules.append(module)

    reference_pdb = case_reference_pdb(case, run_dirs)
    witnesses = [
        extract_case_witnesses(run_dir, module, reference_pdb)
        for run_dir, module in zip(run_dirs, modules)
    ]

    artifact_results = {
        artifact: compare_artifact(artifact, run_dirs)
        for artifact in ARTIFACTS
    }
    return {
        "fixture": f"rigidbody_minimization_{case}",
        "level": "A-job",
        "runs": runs,
        "work_root": str(work_root),
        "run_dirs": [str(run_dir) for run_dir in run_dirs],
        "cns_exec": str(cns_exec),
        "artifacts": artifact_results,
        "witnesses": witnesses,
        "witnesses_equal": witnesses == [witnesses[0]] * len(witnesses),
        "classification": classify_artifacts(artifact_results),
        "golden_reference_dir": str(
            GOLDEN_DATA / "witnesses" / "rigidbody_minimization",
        ),
    }


def run_case(case: str, run_dir: Path) -> tuple[RigidbodyModule, CNSJob]:
    """Run one supported Phase 1 A-job case."""
    if case == "cmrest":
        return run_rigidbody_ajob(run_dir)
    if case == "air_random_removal":
        return run_rigidbody_air_random_removal_ajob(run_dir)
    raise ValueError(f"Unsupported case: {case}")


def case_reference_pdb(case: str, run_dirs: list[Path]) -> Path:
    """Return the RMSD reference PDB for one analysis case."""
    if case == "cmrest":
        return WITNESS_DATA / "rigidbody_1.pdb"
    if case == "air_random_removal":
        return run_dirs[0] / "rigidbody_1.pdb"
    raise ValueError(f"Unsupported case: {case}")


def run_rigidbody_air_random_removal_ajob(path: Path) -> tuple[RigidbodyModule, CNSJob]:
    """Generate and run one rigidbody CNS job with AIR random removal."""
    shutil.copy(AIR_RESTRAINTS, path / AIR_RESTRAINTS.name)
    module = RigidbodyModule(
        order=0,
        path=path,
        initial_params=DEFAULT_RIGIDBODY_CONFIG,
    )
    module.params["cmrest"] = False
    module.params["sampling"] = 1
    module.params["ntrials"] = 1
    module.params["iniseed"] = 917
    module.params["debug"] = True
    module.params["ncores"] = 10
    module.params["mode"] = "local"
    module.params["ambig_fname"] = AIR_RESTRAINTS.name
    module.params["unambig_fname"] = ""
    module.params["hbond_fname"] = ""
    module.params["randremoval"] = True
    module.params["npart"] = 2
    module.params["ranair"] = False
    module.params["surfrest"] = False
    module.params["mol_fix_origin_1"] = True
    module.params["mol_fix_origin_2"] = False
    module.previous_io = PreparedRigidBodyIO(path=path)
    populate_mol_parameters_in_module(
        module.params,
        num_mols=2,
        defaults=module._original_params,
    )

    models_to_dock = module.previous_io.retrieve_models(
        crossdock=module.params["crossdock"],
    )
    module.envvars = module.default_envvars()
    with working_directory(path):
        cns_inputs = module.prepare_cns_input_sequential(
            models_to_dock,
            sampling_factor=1,
            ambig_fnames=None,
        )
        module.output_models = []
        jobs = module.make_cns_jobs(cns_inputs)
        if len(jobs) != 1:
            raise RuntimeError(f"Expected one CNS job, got {len(jobs)}")
        jobs[0].run(compress_out=False, compress_err=False)
    return module, jobs[0]


def extract_case_witnesses(
    run_dir: Path,
    module: RigidbodyModule,
    reference_pdb: Path,
) -> dict[str, Any]:
    """Extract general and AIR-specific witnesses for one generated PDB."""
    pdb_path = run_dir / "rigidbody_1.pdb"
    witnesses = extract_haddock_model_witnesses(
        pdb_path,
        module.params,
        reference_pdb=reference_pdb,
    )
    witnesses["air"] = parse_air_witnesses(pdb_path)
    return witnesses


def parse_air_witnesses(pdb_path: Path) -> dict[str, Any]:
    """Parse AIR violation and cross-validation witnesses from PDB remarks."""
    air: dict[str, Any] = {}
    for line in pdb_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("REMARK energies:"):
            values = [
                float(value.strip())
                for value in line.split(":", 1)[1].split(",")
            ]
            air["energy"] = values[7]
        elif line.startswith("REMARK rms-dev.:"):
            values = [
                float(value.strip())
                for value in line.split(":", 1)[1].split(",")
            ]
            air["rms_dev"] = values[4]
        elif line.startswith("REMARK violations.:"):
            values = [
                float(value.strip())
                for value in line.split(":", 1)[1].split(",")
            ]
            air["violations"] = int(values[0])
        elif line.startswith("REMARK AIRs cross-validation:"):
            values = [
                float(value.strip())
                for value in line.split(":", 1)[1].split(",")
            ]
            air["cv_partition_count"] = int(values[0])
            air["cv_violations"] = int(values[1])
            air["cv_rms"] = values[2]
    return air


def analyze(work_root: Path, runs: int) -> dict[str, Any]:
    """Run all Phase 1 A-job cases repeatedly and compare artifacts."""
    return {
        case: analyze_case(work_root / case, runs, case)
        for case in CASES
    }


def compare_artifact(name: str, run_dirs: list[Path]) -> dict[str, Any]:
    """Compare one generated artifact across all run directories."""
    paths = [run_dir / name for run_dir in run_dirs]
    present = [path.exists() for path in paths]
    result: dict[str, Any] = {
        "present": present,
        "present_in_all_runs": all(present),
    }
    if not all(present):
        result["classification"] = "absent-or-conditional"
        return result

    raw_bytes = [path.read_bytes() for path in paths]
    normalized_bytes = [
        normalize_artifact(path, run_dir)
        for path, run_dir in zip(paths, run_dirs)
    ]
    raw_equal = raw_bytes == [raw_bytes[0]] * len(raw_bytes)
    normalized_equal = normalized_bytes == [normalized_bytes[0]] * len(normalized_bytes)

    result.update(
        {
            "raw_equal": raw_equal,
            "normalized_equal": normalized_equal,
            "raw_checksums": [bytes_sha256(content) for content in raw_bytes],
            "normalized_checksums": [
                bytes_sha256(content)
                for content in normalized_bytes
            ],
            "raw_sizes": [len(content) for content in raw_bytes],
            "normalized_sizes": [len(content) for content in normalized_bytes],
            "classification": artifact_classification(raw_equal, normalized_equal),
        }
    )
    if not raw_equal:
        result["first_raw_diff"] = first_text_diff(raw_bytes[0], raw_bytes[1])
    if not normalized_equal:
        result["first_normalized_diff"] = first_text_diff(
            normalized_bytes[0],
            normalized_bytes[1],
        )
    return result


def normalize_artifact(path: Path, run_dir: Path) -> bytes:
    """Apply the Phase 3 exploratory normalizer for one artifact."""
    if path.suffix == ".pdb":
        return normalize_cns_pdb_for_checksum(path)
    if path.suffix in {".inp", ".out", ".cnserr", ".seed"}:
        return normalize_text_run_paths(path, run_dir)
    return path.read_bytes()


def normalize_text_run_paths(path: Path, run_dir: Path) -> bytes:
    """Normalize line endings and the fresh run directory path."""
    text = path.read_text(encoding="utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(str(run_dir), "<RUN_DIR>")
    text = re.sub(r"\.\./run_\d+", "../<RUN_DIR>", text)
    text = re.sub(
        r"(Program started at:)\s+.*",
        r"\1 <CNS_TIMESTAMP>",
        text,
    )
    text = re.sub(
        r"(Program stopped at:)\s+.*",
        r"\1 <CNS_TIMESTAMP>",
        text,
    )
    text = re.sub(
        r"(CPU time used:)\s+[-+0-9.Ee]+ seconds",
        r"\1 <CPU_SECONDS> seconds",
        text,
    )
    return text.encode("utf-8")


def artifact_classification(raw_equal: bool, normalized_equal: bool) -> str:
    """Classify the artifact in the Phase 3 vocabulary."""
    if raw_equal:
        return "raw-bitwise-stable"
    if normalized_equal:
        return "normalized-bitwise-stable"
    return "not-bitwise-stable-with-current-normalizer"


def classify_artifacts(artifact_results: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Return a compact artifact-to-classification mapping."""
    return {
        name: result["classification"]
        for name, result in artifact_results.items()
    }


def first_text_diff(left: bytes, right: bytes) -> list[str]:
    """Return a short unified diff for the first byte-level difference."""
    left_text = left.decode("utf-8", errors="replace").splitlines()
    right_text = right.decode("utf-8", errors="replace").splitlines()
    diff = difflib.unified_diff(
        left_text,
        right_text,
        fromfile="run_1",
        tofile="run_2",
        lineterm="",
        n=3,
    )
    return list(diff)[:80]


if __name__ == "__main__":
    main()
