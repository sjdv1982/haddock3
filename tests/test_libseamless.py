"""Tests for native CNS cache identity helpers."""

import gzip
from pathlib import Path

import zstandard

from haddock.libs.libseamless import (
    build_canonical_mapping,
    compression_transparent_checksum,
    job_checksum,
    result_checksum,
    synthesize_seamless_run,
    transformation_for_mapping,
    write_cns_dependencies,
)


def _mapping(tmp_path: Path, run_name: str, step_name: str):
    root = tmp_path / run_name / step_name
    module = tmp_path / "install" / "module"
    toppar = tmp_path / "install" / "toppar"
    root.mkdir(parents=True)
    module.mkdir(parents=True, exist_ok=True)
    toppar.mkdir(parents=True, exist_ok=True)
    (root / "renamed-model.pdb").write_text("ATOM\n", encoding="utf-8")
    (module / "protocol.cns").write_text("{ module }\n", encoding="utf-8")
    (toppar / "protein.top").write_text("{ toppar }\n", encoding="utf-8")
    cns = tmp_path / "install" / "cns"
    cns.write_text("#!/bin/sh\n", encoding="utf-8")
    cns.chmod(0o755)
    script = root / "job.inp"
    script.write_text(
        'evaluate ($input_pdb = "renamed-model.pdb")\n'
        "coor @@$input_pdb\n"
        "inline @@MODULE:protocol.cns\n"
        "inline @@TOPPAR:protein.top\n",
        encoding="utf-8",
    )
    return build_canonical_mapping(
        script,
        envvars={"MODULE": str(module), "TOPPAR": str(toppar)},
        cns_exec=cns,
        output_files=[root / "result.pdb"],
        work_dir=root,
    ), root


def test_canonical_mapping_is_independent_of_run_and_step_names(tmp_path):
    first, _ = _mapping(tmp_path, "one", "2_rigidbody")
    second, _ = _mapping(tmp_path, "two", "02_rigidbody")

    assert first.canonical_script == second.canonical_script
    assert first.checksums == second.checksums
    assert first.invariant_dependencies == (
        "canonical-cns",
        "module/protocol.cns",
        "toppar/protein.top",
    )


def test_canonical_mapping_replaces_relative_sibling_dependency_path(tmp_path):
    work_dir = tmp_path / "run" / "01_rigidbody"
    input_pdb = tmp_path / "run" / "data" / "00_topoaa" / "structure_1.pdb"
    module = tmp_path / "install" / "module"
    toppar = tmp_path / "install" / "toppar"
    cns = tmp_path / "install" / "cns"
    work_dir.mkdir(parents=True)
    input_pdb.parent.mkdir(parents=True)
    module.mkdir(parents=True)
    toppar.mkdir(parents=True)
    input_pdb.write_text("ATOM\n", encoding="utf-8")
    cns.write_text("#!/bin/sh\n", encoding="utf-8")
    cns.chmod(0o755)

    mapping = build_canonical_mapping(
        'evaluate ($input_pdb = "../data/00_topoaa/structure_1.pdb")\n'
        "coor @@$input_pdb\n",
        envvars={"MODULE": str(module), "TOPPAR": str(toppar)},
        cns_exec=cns,
        output_files=[work_dir / "result.pdb"],
        work_dir=work_dir,
    )

    assert "canonical-input-1.pdb" in mapping.canonical_script
    assert "00_topoaa" not in mapping.canonical_script


def test_canonical_mapping_replaces_equivalent_job_path_spelling(tmp_path):
    work_dir = tmp_path / "run" / "01_rigidbody"
    input_pdb = work_dir / "structure_1.pdb"
    module = tmp_path / "install" / "module"
    toppar = tmp_path / "install" / "toppar"
    cns = tmp_path / "install" / "cns"
    work_dir.mkdir(parents=True)
    module.mkdir(parents=True)
    toppar.mkdir(parents=True)
    input_pdb.write_text("ATOM\n", encoding="utf-8")
    cns.write_text("#!/bin/sh\n", encoding="utf-8")
    cns.chmod(0o755)

    mapping = build_canonical_mapping(
        'evaluate ($input_pdb = "../01_rigidbody/structure_1.pdb")\n'
        "coor @@$input_pdb\n",
        envvars={"MODULE": str(module), "TOPPAR": str(toppar)},
        cns_exec=cns,
        output_files=[work_dir / "result.pdb"],
        work_dir=work_dir,
    )

    assert "canonical-input-1.pdb" in mapping.canonical_script
    assert "01_rigidbody" not in mapping.canonical_script


def test_compression_transparent_checksum_and_manifest(tmp_path):
    plain = tmp_path / "input.pdb"
    compressed = tmp_path / "input.pdb.gz"
    plain.write_bytes(b"ATOM\n")
    with gzip.open(compressed, "wb") as handle:
        handle.write(plain.read_bytes())
    mapping, step = _mapping(tmp_path, "run", "1_rigidbody")

    assert compression_transparent_checksum(plain) == compression_transparent_checksum(compressed)
    write_cns_dependencies(step, mapping)
    write_cns_dependencies(step, mapping)
    assert (step / "CNS_DEPENDENCIES").read_text(encoding="utf-8") == (
        "canonical-cns\nmodule/protocol.cns\ntoppar/protein.top\n"
    )


def test_compression_transparent_checksum_supports_zstd(tmp_path):
    plain = tmp_path / "input.pdb"
    compressed = tmp_path / "input.pdb.zst"
    plain.write_bytes(b"ATOM\n")
    compressed.write_bytes(zstandard.ZstdCompressor().compress(plain.read_bytes()))

    assert compression_transparent_checksum(plain) == compression_transparent_checksum(compressed)


def test_checksum_and_synthesized_workspace_use_the_same_mapping(tmp_path):
    mapping, step = _mapping(tmp_path, "run", "1_rigidbody")
    (mapping.output_paths[0]).write_text("REMARK score: 1.0\n", encoding="utf-8")

    checksum, transformation = transformation_for_mapping(mapping)
    staged = synthesize_seamless_run(mapping, step / ".cache-stage" / checksum)

    assert job_checksum(mapping) == checksum
    assert transformation["__output__"] == ("result", "bytes", None)
    assert result_checksum(mapping) == compression_transparent_checksum(mapping.output_paths[0])
    assert (staged.stage_dir / "canonical.inp").read_text(encoding="utf-8") == mapping.canonical_script
    assert "canonical-cns" in staged.manifest.read_text(encoding="utf-8")
    assert "REMARK DATE:" in staged.wrapper.read_text(encoding="utf-8")
