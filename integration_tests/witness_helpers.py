"""Helpers for CNS witness-style integration tests."""

from __future__ import annotations

import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import numpy as np
import yaml

from haddock.gear.haddockmodel import HaddockModel
from seamless.checksum.calculate_checksum import calculate_checksum


WEIGHT_KEYS = ("w_vdw", "w_elec", "w_desolv", "w_air", "w_bsa")
REGIME_GATE_OVERRIDES = {
    "R1": {
        "dependencies": "exact",
        "artifacts": "normalized_bitwise",
        "witnesses": "exact",
    },
    "R2": {
        "dependencies": "exact",
        "artifacts": "record_only",
        "witnesses": "band",
    },
}


def load_baseline(path: Path) -> dict[str, Any]:
    """Load a path-oriented witness baseline."""
    with open(path, encoding="utf-8") as handle:
        baseline = yaml.safe_load(handle)
    if baseline.get("schema_version") != 1:
        raise ValueError(f"Unsupported witness schema: {baseline.get('schema_version')}")
    return baseline


def materialize_reference_paths(base_dir: Path, logical_paths: list[str]) -> dict[str, Path]:
    """Resolve direct reference paths or their checksum/index sidecars."""
    resolved: dict[str, Path] = {}
    for logical_path in logical_paths:
        rel_path = Path(logical_path)
        direct = base_dir / rel_path
        checksum_sidecar = base_dir / f"{logical_path}.CHECKSUM"
        index_sidecar = base_dir / f"{logical_path.rstrip('/')}.INDEX"
        if direct.exists():
            resolved[logical_path] = direct
        elif checksum_sidecar.exists():
            resolved[logical_path] = checksum_sidecar
        elif index_sidecar.exists():
            resolved[logical_path] = index_sidecar
        else:
            raise FileNotFoundError(f"No reference path or sidecar for {logical_path}")
    return resolved


def extract_haddock_model_witnesses(
    model_pdb: Path,
    weights: dict[str, float],
    *,
    reference_pdb: Path | None = None,
    rmsd_key: str = "rmsd_to_reference",
) -> dict[str, Any]:
    """Extract score, energy, and optional RMSD witnesses from a PDB."""
    model = HaddockModel(model_pdb)
    score_weights = {key: weights[key] for key in WEIGHT_KEYS}
    witnesses = {
        "haddock_score": model.calc_haddock_score(**score_weights),
        "unw_energies": {
            "vdw": model.energies["vdw"],
            "elec": model.energies["elec"],
            "desolv": model.energies["desolv"],
        },
    }
    if reference_pdb is not None:
        witnesses[rmsd_key] = aligned_common_heavy_atom_rmsd(reference_pdb, model_pdb)
    return witnesses


def extract_emscoring_witnesses(
    input_pdb: Path,
    scored_pdb: Path,
    weights: dict[str, float],
) -> dict[str, Any]:
    """Extract Phase 0 scoring witnesses from an emscoring output PDB."""
    return extract_haddock_model_witnesses(
        scored_pdb,
        weights,
        reference_pdb=input_pdb,
        rmsd_key="rmsd_input_to_scored",
    )


def apply_gate_profile(
    baseline: dict[str, Any],
    *,
    reference_dir: Path,
    generated_dir: Path,
    witnesses: dict[str, Any],
    regime: str | None = None,
    normalizers: dict[str, Callable[[Path], bytes]] | None = None,
) -> None:
    """Apply dependency, artifact, and witness gates declared by a baseline."""
    baseline = with_regime_profile(baseline, regime) if regime else baseline
    gate = baseline["gate"]
    if gate["dependencies"] == "exact":
        _assert_paths_present(reference_dir, baseline["dependencies"].get("files", []))

    if gate["artifacts"] == "bitwise":
        _assert_artifacts_bitwise(
            reference_dir,
            generated_dir,
            baseline["artifacts"].get("raw", []),
        )
    elif gate["artifacts"] == "normalized_bitwise":
        _assert_artifacts_bitwise(
            reference_dir,
            generated_dir,
            baseline["artifacts"].get("raw", []),
            normalizers=normalizers,
        )
    elif gate["artifacts"] == "record_only":
        _assert_paths_present(reference_dir, baseline["artifacts"].get("raw", []))
    else:
        raise ValueError(f"Unsupported artifact gate: {gate['artifacts']}")

    if gate["witnesses"] == "band":
        _assert_witness_bands(baseline["witnesses"], witnesses)
    elif gate["witnesses"] == "exact":
        _assert_witness_exact(baseline["witnesses"], witnesses)
    elif gate["witnesses"] != "off":
        raise ValueError(f"Unsupported witness gate: {gate['witnesses']}")


def with_regime_profile(baseline: dict[str, Any], regime: str) -> dict[str, Any]:
    """Return a baseline copy with an R1/R2 gate profile applied."""
    if regime not in REGIME_GATE_OVERRIDES:
        raise ValueError(f"Unsupported witness regime: {regime}")
    profiled_baseline = deepcopy(baseline)
    profiled_baseline["regime"] = regime
    profiled_baseline["gate"] = {
        **profiled_baseline["gate"],
        **REGIME_GATE_OVERRIDES[regime],
    }
    return profiled_baseline


def file_sha256(path: Path) -> str:
    """Compute a SHA-256 digest for a file."""
    return bytes_sha256(path.read_bytes())


def bytes_sha256(content: bytes) -> str:
    """Compute a Seamless-compatible SHA-256 digest for bytes."""
    return calculate_checksum(content)


def normalize_cns_pdb_for_checksum(path: Path) -> bytes:
    """Return CNS PDB bytes without run-volatile header lines."""
    volatile_prefixes = (
        "REMARK FILENAME=",
        "REMARK initial structure ",
        "REMARK DATE:",
    )
    stable_lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith(volatile_prefixes)
    ]
    return ("\n".join(stable_lines) + "\n").encode("utf-8")


def normalize_emscoring_pdb_for_checksum(path: Path) -> bytes:
    """Return emscoring PDB bytes without CNS run-volatile header lines."""
    return normalize_cns_pdb_for_checksum(path)


def write_checksum_sidecar(
    path: Path,
    normalizer: Callable[[Path], bytes] | None = None,
) -> Path:
    """Write a Seamless .CHECKSUM sidecar for a file."""
    checksum = _path_checksum(path, normalizer)
    sidecar = path.with_name(f"{path.name}.CHECKSUM")
    sidecar.write_text(f"{checksum}\n", encoding="utf-8")
    return sidecar


def aligned_common_heavy_atom_rmsd(reference_pdb: Path, mobile_pdb: Path) -> float:
    """Calculate aligned RMSD over common heavy atoms."""
    reference_atoms = _heavy_atom_coordinates(reference_pdb)
    mobile_atoms = _heavy_atom_coordinates(mobile_pdb)
    common_keys = sorted(reference_atoms.keys() & mobile_atoms.keys())
    if not common_keys:
        raise ValueError("No common heavy atoms found for RMSD calculation")

    reference = np.asarray([reference_atoms[key] for key in common_keys], dtype=float)
    mobile = np.asarray([mobile_atoms[key] for key in common_keys], dtype=float)
    return _kabsch_rmsd(reference, mobile)


def _assert_paths_present(base_dir: Path, paths: list[str]) -> None:
    materialize_reference_paths(base_dir, paths)


def _assert_artifacts_bitwise(
    reference_dir: Path,
    generated_dir: Path,
    logical_paths: list[str],
    normalizers: dict[str, Callable[[Path], bytes]] | None = None,
) -> None:
    for logical_path in logical_paths:
        generated_path = generated_dir / logical_path
        if not generated_path.exists():
            raise AssertionError(f"Generated artifact missing: {logical_path}")
        normalizer = normalizers.get(logical_path) if normalizers else None
        generated_checksum = _path_checksum(generated_path, normalizer)
        reference_path = _artifact_reference_path(reference_dir, logical_path)
        if reference_path.name.endswith(".CHECKSUM"):
            expected_checksum = reference_path.read_text(encoding="utf-8").strip()
        else:
            expected_checksum = _path_checksum(reference_path, normalizer)
        assert generated_checksum == expected_checksum, logical_path


def _artifact_reference_path(reference_dir: Path, logical_path: str) -> Path:
    checksum_sidecar = reference_dir / f"{logical_path}.CHECKSUM"
    if checksum_sidecar.exists():
        return checksum_sidecar
    return materialize_reference_paths(reference_dir, [logical_path])[logical_path]


def _path_checksum(path: Path, normalizer: Callable[[Path], bytes] | None = None) -> str:
    if normalizer:
        return bytes_sha256(normalizer(path))
    return file_sha256(path)


def _assert_witness_bands(expected: dict[str, Any], observed: dict[str, Any]) -> None:
    for key, expected_value in expected.items():
        observed_value = observed[key]
        if "expected" in expected_value:
            tolerance = expected_value["abs"]
            assert math.isclose(
                observed_value,
                expected_value["expected"],
                abs_tol=tolerance,
                rel_tol=0.0,
            ), f"{key}: observed {observed_value}, expected {expected_value}"
        else:
            _assert_witness_bands(expected_value, observed_value)


def _assert_witness_exact(expected: dict[str, Any], observed: dict[str, Any]) -> None:
    for key, expected_value in expected.items():
        observed_value = observed[key]
        if "expected" in expected_value:
            assert observed_value == expected_value["expected"], key
        else:
            _assert_witness_exact(expected_value, observed_value)


def _heavy_atom_coordinates(pdb_path: Path) -> dict[tuple[str, str, str, str, str], np.ndarray]:
    atoms: dict[tuple[str, str, str, str, str], np.ndarray] = {}
    with open(pdb_path, encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            atom_name = line[12:16].strip()
            element = line[76:78].strip() or atom_name[:1]
            if element.upper().startswith("H") or atom_name.upper().startswith("H"):
                continue
            key = (
                line[21].strip(),
                line[22:26].strip(),
                line[26].strip(),
                line[17:20].strip(),
                atom_name,
            )
            atoms[key] = np.array(
                [
                    float(line[30:38]),
                    float(line[38:46]),
                    float(line[46:54]),
                ],
                dtype=float,
            )
    return atoms


def _kabsch_rmsd(reference: np.ndarray, mobile: np.ndarray) -> float:
    reference_centered = reference - reference.mean(axis=0)
    mobile_centered = mobile - mobile.mean(axis=0)
    covariance = mobile_centered.T @ reference_centered
    left, _, right_t = np.linalg.svd(covariance)
    rotation = left @ right_t
    if np.linalg.det(rotation) < 0:
        left[:, -1] *= -1
        rotation = left @ right_t
    aligned = mobile_centered @ rotation
    diff = aligned - reference_centered
    return float(np.sqrt((diff * diff).sum() / len(reference)))
