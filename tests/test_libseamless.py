"""Tests for native CNS cache identity helpers."""

import gzip
from pathlib import Path

from haddock.libs.libseamless import (
    build_canonical_mapping,
    compression_transparent_checksum,
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
