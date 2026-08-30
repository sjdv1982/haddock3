"""Tests for native CNS cache identity helpers."""

import gzip
from pathlib import Path
from types import SimpleNamespace

import zstandard

import haddock.libs.libseamless as libseamless
from haddock.libs.libseamless import (
    build_canonical_mapping,
    canonical_mapping_for_job,
    compression_transparent_checksum,
    job_checksum,
    precompute_invariant_checksums_for_jobs,
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


def test_precomputed_invariants_preserve_mapping_and_exclude_model_input(tmp_path):
    mapping, step = _mapping(tmp_path, "run", "1_rigidbody")
    job = SimpleNamespace(
        input_file=step / "job.inp",
        envvars={
            "MODULE": str(tmp_path / "install" / "module"),
            "TOPPAR": str(tmp_path / "install" / "toppar"),
        },
        cns_exec=mapping.cns_exec,
        work_dir=step,
    )

    manifests, checksums = precompute_invariant_checksums_for_jobs([job])
    cached_mapping = build_canonical_mapping(
        job.input_file,
        envvars=job.envvars,
        cns_exec=job.cns_exec,
        output_files=[step / "result.pdb"],
        work_dir=step,
        invariant_checksums=checksums,
    )

    assert manifests[step] == set(mapping.invariant_dependencies)
    assert cached_mapping.checksums == mapping.checksums
    assert (step / "renamed-model.pdb").resolve() not in checksums


def test_precomputed_dependency_scan_is_reused_across_job_indexes(
    tmp_path, monkeypatch
):
    mapping, step = _mapping(tmp_path, "run", "1_rigidbody")
    base_script = (step / "job.inp").read_text(encoding="utf-8")
    jobs = []
    for index in (1, 2):
        output = step / f"result_{index}.pdb"
        jobs.append(
            SimpleNamespace(
                input_file=(
                    base_script
                    + f'evaluate ($seed = {1000 + index})\n'
                    + f'evaluate ($count = {index})\n'
                    + f'evaluate ($output_pdb_filename = "{output.name}")\n'
                ),
                envvars={
                    "MODULE": str(tmp_path / "install" / "module"),
                    "TOPPAR": str(tmp_path / "install" / "toppar"),
                },
                cns_exec=mapping.cns_exec,
                work_dir=step,
                output_files=[output],
                output_pdb_files=[output],
            )
        )

    expected_mappings = [
        build_canonical_mapping(
            job.input_file,
            envvars=job.envvars,
            cns_exec=job.cns_exec,
            output_files=job.output_files,
            output_pdb_files=job.output_pdb_files,
            work_dir=job.work_dir,
        )
        for job in jobs
    ]

    scan_calls = 0
    checksum_calls = 0
    original_scan = libseamless.scan_cns_dependencies
    original_checksum = libseamless.compression_transparent_checksum

    def counted_scan(*args, **kwargs):
        nonlocal scan_calls
        scan_calls += 1
        return original_scan(*args, **kwargs)

    def counted_checksum(*args, **kwargs):
        nonlocal checksum_calls
        checksum_calls += 1
        return original_checksum(*args, **kwargs)

    monkeypatch.setattr(libseamless, "scan_cns_dependencies", counted_scan)
    monkeypatch.setattr(
        libseamless, "compression_transparent_checksum", counted_checksum
    )
    precompute_invariant_checksums_for_jobs(jobs)
    assert checksum_calls == 4
    first = canonical_mapping_for_job(jobs[0])
    second = canonical_mapping_for_job(jobs[1])

    assert scan_calls == 1
    assert jobs[0].cache_dependency_scan is jobs[1].cache_dependency_scan
    assert jobs[0].cache_mapping_template is jobs[1].cache_mapping_template
    assert first == expected_mappings[0]
    assert second == expected_mappings[1]
    assert first.dependencies == second.dependencies
    assert first.canonical_script != second.canonical_script
    assert job_checksum(first) != job_checksum(second)


def test_precomputed_dependency_scan_keeps_distinct_model_inputs(
    tmp_path, monkeypatch
):
    mapping, step = _mapping(tmp_path, "run", "1_rigidbody")
    other_model = step / "other-model.pdb"
    other_model.write_text("ATOM other\n", encoding="utf-8")
    first_script = (step / "job.inp").read_text(encoding="utf-8")
    second_script = first_script.replace("renamed-model.pdb", other_model.name)
    jobs = [
        SimpleNamespace(
            input_file=script,
            envvars={
                "MODULE": str(tmp_path / "install" / "module"),
                "TOPPAR": str(tmp_path / "install" / "toppar"),
            },
            cns_exec=mapping.cns_exec,
            work_dir=step,
        )
        for script in (first_script, second_script)
    ]

    scan_calls = 0
    original_scan = libseamless.scan_cns_dependencies

    def counted_scan(*args, **kwargs):
        nonlocal scan_calls
        scan_calls += 1
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(libseamless, "scan_cns_dependencies", counted_scan)
    precompute_invariant_checksums_for_jobs(jobs)

    assert scan_calls == 2
    assert jobs[0].cache_dependency_scan is not jobs[1].cache_dependency_scan


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


def test_canonical_mapping_accepts_dependency_named_after_its_canonical_role(tmp_path):
    """A basename that the canonical name contains is not a leak.

    ``ambig.tbl`` is rewritten to ``canonical-ambig.tbl``, which contains the
    original basename as a substring.  Scanning the rewritten script without
    masking the substituted names reported that as a location leak and made
    every job reading such a file uncacheable.
    """
    work_dir = tmp_path / "run" / "01_rigidbody"
    module = tmp_path / "install" / "module"
    toppar = tmp_path / "install" / "toppar"
    work_dir.mkdir(parents=True)
    module.mkdir(parents=True)
    toppar.mkdir(parents=True)
    restraints = tmp_path / "run" / "data" / "ambig.tbl"
    restraints.parent.mkdir(parents=True, exist_ok=True)
    restraints.write_text("assign\n", encoding="utf-8")
    (module / "protocol.cns").write_text("{ module }\n", encoding="utf-8")
    (toppar / "protein.top").write_text("{ toppar }\n", encoding="utf-8")
    cns = tmp_path / "install" / "cns"
    cns.write_text("#!/bin/sh\n", encoding="utf-8")
    cns.chmod(0o755)
    script = work_dir / "job.inp"
    script.write_text(
        f'evaluate ($ambig_fname = "{restraints}")\n'
        "noe @@$ambig_fname end\n"
        "inline @@MODULE:protocol.cns\n"
        "inline @@TOPPAR:protein.top\n",
        encoding="utf-8",
    )

    mapping = build_canonical_mapping(
        script,
        envvars={"MODULE": str(module), "TOPPAR": str(toppar)},
        cns_exec=cns,
        output_files=[work_dir / "result.pdb"],
        work_dir=work_dir,
    )

    assert "canonical-ambig.tbl" in mapping.canonical_script
    assert str(restraints) not in mapping.canonical_script
