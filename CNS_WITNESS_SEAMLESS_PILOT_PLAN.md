# CNS Witness and Seamless Pilot Plan

## Purpose

This pilot is the first vertical slice of a future HADDOCK3 witness-centered
testing system for CNS and CNS replacements. It should be small in coverage but
production-shaped in architecture: the single-scoring and rigidbody cases are
the first populated fixtures, not disposable special-case harnesses.

The immediate motivation is that a CNS replacement needs tests that verify more
than "CNS ran" or "a PDB was produced". The pilot must verify scientific
witnesses such as HADDOCK score and RMSD, while also preparing exact artifact
identity and dependency identity checks for the broader R1/R2/R3 regime.

## Core Model

The system has three independent layers:

1. **Dependency layer**

   Records the complete declared identity of the computation inputs: generated
   `.inp` files, included `.cns` files, `TOPPAR` files, PDB/PSF inputs,
   restraints, relevant config values, CNS executable identity, and the
   environment variables that affect CNS resolution.

2. **Artifact layer**

   Records the identity of produced artifacts in the least indirect useful form.
   For small A-job/A-body tests, whole input files, generated CNS inputs, and
   output PDB files can simply be committed to Git and used directly as the
   baseline. Checksums do not need to be duplicated for those files unless they
   serve another purpose. For larger files or multi-file outputs, Git may store
   the corresponding Seamless sidecar instead: `file.ext.CHECKSUM` for a file or
   `dir.INDEX` for a folder/multi-file collection. Seamless tooling can
   materialize the real path when needed.

3. **Witness layer**

   Records scientific observables extracted from artifacts: HADDOCK score,
   unweighted energies, RMSD to a reference structure, atom/model counts,
   distribution summaries, CAPRI stars, cluster-level summaries, and related
   domain witnesses.

The R1/R2/R3 regimes should be implemented as named gate profiles over these
same three layers. They are not separate test implementations.

Example interpretation:

- **R1**: dependency identity matches, artifact identity matches, and witnesses
  match tightly.
- **R2**: dependency identity matches, artifacts may match after normalization
  or be diagnostic only, and witnesses match tightly.
- **R3**: dependencies are recorded well enough to explain the run, artifacts
  are diagnostic, and witnesses pass broader scientific acceptance bands.

## Baseline and Gate Schema

Each witness fixture should declare how it gates and which logical paths are
being compared. Keep the schema path-oriented: use real paths such as
`file.ext` and `dir/`, not storage descriptors. What Git actually contains is a
storage/materialization detail: it may be the real `file.ext`, the sidecar
`file.ext.CHECKSUM`, the real `dir/`, or the deep sidecar `dir.INDEX`.

Use this schema shape for the pilot baselines:

```yaml
schema_version: 1
fixture: rigidbody_minimization
level: A-job
regime: R2

gate:
  dependencies: exact
  artifacts: record_only        # record_only | bitwise | normalized_bitwise
  witnesses: band               # off | band | exact

witnesses:
  haddock_score:
    expected: -123.456
    abs: 1.0e-3
  rmsd_to_reference:
    expected: 0.0
    abs: 1.0e-4
  unw_energies:
    vdw:
      expected: -12.345
      abs: 1.0e-3
    elec:
      expected: -23.456
      abs: 1.0e-3

artifacts:
  raw:
    - rigidbody_1.reference.pdb
  normalized:
    - rigidbody_1.out

dependencies:
  files:
    - rigidbody_1.inp
    - e2aP_1F3G_haddock.pdb
    - hpr_ensemble_1_haddock.pdb
  collections:
    - resolved_cns_inputs/
```

Keep these top-level sections stable in the pilot: dependencies, artifacts,
witnesses, and gates. The schema names logical paths, and R1/R2/R3 are profiles
that combine these layers. The test loader is responsible for resolving whether
a logical path is present directly or must be materialized from
`file.ext.CHECKSUM` or `dir.INDEX`.

Gate semantics:

- In `bitwise` mode, compare checksums, not raw file bytes directly. For each
  logical file path, the reference may be either the materialized `file.ext` or
  the sidecar `file.ext.CHECKSUM`. The generated `file.ext` is converted to a
  checksum and compared to the reference checksum.
- In `normalized_bitwise` mode, first normalize both reference and generated
  artifacts according to the fixture's normalizer, then compare checksums of the
  normalized forms.
- For logical folder paths such as `dir/`, the reference may be the materialized
  folder or `dir.INDEX`. The generated folder is converted to the same deep
  index form before comparison.
- In `record_only` mode, write or update diagnostic sidecars if requested, but
  do not fail the test on artifact identity.

## Implementation Locations

Use these locations for the pilot:

- Put reusable witness helpers in `integration_tests/witness_helpers.py`.
- Put Phase 0 tests in `integration_tests/test_witness_cns_scoring.py`.
- Put Phase 1 and Phase 2 tests in
  `integration_tests/test_witness_rigidbody.py`.
- Put committed witness baselines and small A-job artifacts under
  `integration_tests/golden_data/witnesses/`.
- Put Phase 3 reproducibility tooling in `devtools/` and document its output in
  `docs/testing/`.

The helper layer should expose four small operations: load a path-oriented
baseline, materialize reference paths from `.CHECKSUM` or `.INDEX` sidecars when
needed, extract witnesses from generated artifacts, and apply the selected gate
profile.

Baseline generation must be an explicit developer action, not normal pytest
behavior. The implementation may provide a small devtool or pytest option to
write the initial current-CNS baselines, but ordinary test runs must only read
and compare committed baselines.

## Phase 0: Single CNS Scoring Witness

Before rigidbody minimization, add a smaller protocol that isolates CNS scoring
and scored-coordinate writing:

1. Take a prepared input PDB/PSF pair.
2. Run `emscoring` once with `nemsteps = 0`.
3. Write the scored coordinates.

This is the simplest CNS replacement witness for "can reproduce HADDOCK scoring
for a fixed coordinate set". It should be an A-job/A-body style test, not a full
workflow test.

Important HADDOCK behavior:

- The CNS-backed `emscoring` module is not a pure "evaluate this coordinate set"
  operation by default. Its CNS script performs a Powell energy minimization
  before final scoring and coordinate writing.
- The minimization is controlled by `nemsteps`. The default is nonzero
  (`emscoring` defaults to 50), but `nemsteps = 0` disables that minimization
  block.
- The `emscoring` module writes a scored PDB in its step directory
  (`emscoring_1.pdb` for the first model) in addition to `emscoring.tsv`, so the
  Phase 0 RMSD witness is meaningful.
- The `haddock3-score` CLI also uses `emscoring`; its `--outputpdb` option saves
  an extra copy of that minimized/scored output PDB outside the run directory.

Default pilot setup:

- Use `integration_tests/golden_data/ab_ag_BHL.pdb` with
  `integration_tests/golden_data/ab_ag_BHL.psf` as the prepared PDB/PSF pair.
  This pair is already used by the `emscoring` integration tests, is all-protein,
  and avoids running `topoaa` inside the Phase 0 A-job harness.
- Set `nemsteps = 0` so the witness isolates scoring and score-header/PDB
  writing rather than minimization.
- Set `per_interface_scoring = false` for the first fixture. Interface-specific
  header witnesses can be added later as a separate fixture.
- Gate on HADDOCK score, unweighted energy terms, and RMSD from input to scored
  PDB over common heavy atoms after alignment.
- Treat the scored PDB as the committed A-body reference artifact when it is
  small enough for Git.
- Keep a default-`nemsteps` scoring fixture out of Phase 0; minimization behavior
  is covered by the later rigidbody/minimization phases.

Suggested witnesses:

```yaml
witnesses:
  haddock_score:
    expected: -123.456
    abs: 1.0e-3
  unw_energies:
    vdw:
      expected: -12.345
      abs: 1.0e-3
    elec:
      expected: -23.456
      abs: 1.0e-3
    desolv:
      expected: -3.210
      abs: 1.0e-3
  rmsd_input_to_scored:
    expected: 0.0
    abs: 1.0e-4
```

The implementer must generate the concrete numeric baseline values from one
current-CNS run and commit those values in the baseline file; placeholders must
not remain in committed baselines.

This phase should answer whether a CNS replacement can reproduce the basic
score/write behavior before testing rigidbody sampling or minimization.

## Phase 1: A-job Rigidbody Witness

Add a representative rigidbody minimization test at A-job level. This should use
one generated CNS input and one direct `CNSJob`, not the current narrow
`test_cnsjob.py` fixture that only proves CNS can write a PDB.

Concrete fixture:

- Use the E2A/HPR protein-protein system from the existing protein-protein
  docking example:
  `examples/docking-protein-protein/docking-protein-protein-test.cfg`.
- For the A-job harness, use the already prepared single-model topology fixtures
  used by the current rigidbody integration test:
  `tests/golden_data/e2aP_1F3G_haddock.pdb`,
  `tests/golden_data/e2aP_1F3G_haddock.psf`,
  `tests/golden_data/hpr_ensemble_1_haddock.pdb`, and
  `tests/golden_data/hpr_ensemble_1_haddock.psf`.
- Use `examples/docking-protein-protein/data/e2a-hpr_1GGR.pdb` only as optional
  biological context for later CAPRI/RMSD checks; the Phase 1 RMSD gate should
  compare against the committed current-CNS rigidbody output reference.

Implementation shape:

- Generate one rigidbody `.inp` from the concrete fixture with:
  `cmrest = true`, `sampling = 1`, `ntrials = 1`, `iniseed = 917`,
  `debug = true`, `ncores = 1`, and `mode = local`.
- Leave `ambig_fname`, `unambig_fname`, and `hbond_fname` empty; set
  `ranair = false` and `surfrest = false`. This keeps the test on
  center-of-mass restraints and avoids depending on AIR generation.
- Note that rigidbody uses `seed = iniseed + model_index`, so the first generated
  CNS job should use seed `918` when `iniseed = 917`.
- Run exactly one `CNSJob`.
- Parse the output PDB with `HaddockModel`.
- Commit the small A-job input files, generated `.inp`, and output PDB where
  that is clearer than committing checksums.
- Gate on HADDOCK score and RMSD to the reference PDB.
- Record unweighted energies as additional witnesses.
- Use Seamless sidecars only where they add value: for large files, multi-file
  dependency sets, normalized artifacts, or future materialization.

Default gate:

- Dependencies: exact comparison of the logical paths listed in the schema,
  after materializing any sidecars needed by the test environment.
- Artifacts: record-only until phase 3 proves bitwise or normalized-bitwise
  stability.
- Witnesses: band-gated.

The RMSD witness should be expected to be `0.0` when the committed reference is
generated by the same current-CNS run. That makes it both easy to interpret and
useful for a CNS replacement: a score-only match with coordinate drift should
still be visible.

## Phase 2: A-module Rigidbody Witness

Add a module-level rigidbody test using the same deterministic scientific setup.
This test should exercise the normal `RigidbodyModule.run()` path and verify that
the witnesses survive the module abstraction, PDB remark parsing, `PDBFile`
fields, and `io.json` export.

Model this phase on
`examples/docking-protein-protein/docking-protein-protein-test.cfg`, but trim it
to the rigidbody module boundary and replace the AIR-driven restraint setup with
center-of-mass restraints. The purpose is not to retest the whole example; it is
to run the real module code on the same protein-protein CM-restraint science
case used in Phase 1.

Implementation shape:

- Run `RigidbodyModule` with the E2A/HPR prepared topology fixture setup from
  Phase 1.
- Reuse the protein-protein example's molecules/science case, but do not use its
  `data/e2a-hpr_air.tbl` AIR file in the pilot.
- Use center-of-mass restraints instead:
  `cmrest = true`, `ambig_fname = ""`, `unambig_fname = ""`,
  `hbond_fname = ""`, `ranair = false`, and `surfrest = false`.
- Keep the remaining rigidbody scoring/minimization parameters at module
  defaults.
- Override for the pilot harness:
  `sampling = 100`, `ntrials = 1`, `iniseed = 917`, `ncores = 1`,
  `mode = local`, and `debug = true`.
- Require exactly 100 generated output models: `rigidbody_1.pdb` through
  `rigidbody_100.pdb`. With `iniseed = 917`, their CNS seeds should be `918`
  through `1017`.
- Parse generated PDB remarks with `HaddockModel`.
- Compare parsed score and unweighted energies to `PDBFile.score`,
  `PDBFile.unw_energies`, and exported `io.json`.
- Gate on ensemble-level witnesses, not just one structure:
  output count, score distribution, best score, best-model index, per-model
  unweighted-energy vectors, and RMSD distribution after sorting by score.
- Add an R3-level score-count witness. Let `X` be the tenth-best HADDOCK score
  of the current-CNS reference run, with lower scores considered better. The
  generated run must contain at least 10 structures with HADDOCK score `<= X`.
- Add an R2-level RMSD-table witness. Sort the current-CNS reference run by
  HADDOCK score and take the best-scoring reference structure as the RMSD
  target. Sort the generated run by HADDOCK score, calculate each generated
  structure's RMSD to that target, and compare the resulting 100-row RMSD vector
  against the committed reference RMSD table within a tolerance band. The first
  reference row has RMSD `0.0` by construction.
- List module output collections as real paths such as `rigidbody_outputs/` in
  the schema; Git may store `rigidbody_outputs.INDEX` instead of the full output
  tree.
- Do not reuse the Phase 1 single A-job reference as the only RMSD baseline.
  Phase 2 must have multi-model references or sidecars for the 100-output
  collection.

Default gate:

- Dependencies: exact comparison of the logical paths listed in the schema,
  after materializing any sidecars needed by the test environment.
- Artifacts: record-only or normalized-bitwise only where proven stable.
- Witnesses: band-gated.

This test should make the existing rigidbody smoke checks secondary. File
existence remains useful, but the primary assertion is that the module produced
the expected scientific witnesses and serialized them correctly.

## Phase 3: CNS Artifact Reproducibility Analysis

Analyze whether CNS output writing is bitwise reproducible, can be made
normalized-bitwise reproducible, or should remain witness-gated.

Implementation shape:

- Re-run the same phase-1 A-job multiple times in fresh directories.
- Compare raw bytes for `.inp`, `.pdb`, `.out`, sidecars, and any seed/error
  files.
- If raw bytes differ, test normalized variants that remove known volatile
  content such as timestamps, absolute paths, CNS banners, or run-directory
  noise.
- Inspect the relevant CNS/HADDOCK sources, including `print_coorheader.cns`,
  `write coordinates format=pdbo`, generated CNS headers, and CNS stdout.
- Produce a short report classifying each artifact as:
  - raw bitwise stable,
  - normalized-bitwise stable,
  - witness-stable only,
  - unstable or not suitable for gating.

This phase decides where R1 is legitimate. It should not force bitwise gates
before the evidence exists.

## Phase 4: Seamless CNS Mode

Add a Seamless CNS execution mode in config, using `seamless-run` from the
`seamless-suite` pip package. The initial mode should wrap CNS jobs without
rewriting the module workflow.

Implementation shape:

- Add `seamless` as a valid execution `mode`.
- Add a `SeamlessScheduler` behind `get_engine("seamless", params)`.
- Keep the cache unit at one generated `CNSJob`.
- Wrap the CNS command with `seamless-run`.
- Preserve expected output paths so existing modules do not need output handling
  rewrites.
- Keep `SEAMLESS_CACHE` user-managed.
- If `seamless-run` is missing, fail with a clear message pointing to
  `seamless-suite`.

Dependency analysis is the hard requirement for this phase. The wrapper must not
hide CNS dependencies inside ambient filesystem state.

The dependency scanner should resolve:

- generated `.inp` files,
- `@MODULE:` and `@@MODULE:` CNS includes,
- `TOPPAR:` files,
- direct `@@path` file reads,
- PDB and PSF inputs,
- ambiguous, unambiguous, hydrogen-bond, dihedral, tensor, and other restraint
  files,
- ligand topology and parameter files,
- relevant CNS execution environment variables such as `MODULE`, `MODDIR`, and
  `TOPPAR`.

It should distinguish read dependencies from declared outputs such as output
PDBs, CNS stdout logs, seed files, and `.cnserr` files. In Seamless mode,
unresolved read dependencies should be a hard failure.

## Optional Dependencies

Keep Seamless dependencies out of ordinary HADDOCK runtime unless explicitly
needed.

- Use the Seamless core package for checksum and deep-index sidecar generation.
- Use `seamless-suite` for `seamless-run` execution.
- Tests that need these packages should skip gracefully when they are absent,
  except in CI jobs explicitly configured to exercise Seamless support.

## Test Cases

Add tests in increasing order of dependence:

- Unit tests for gate evaluation: `record_only`, `bitwise`,
  `normalized_bitwise`, `band`, and named R1/R2/R3 profiles.
- Unit tests for RMSD witness calculation, including self-reference RMSD `0.0`.
- Unit tests for score and unweighted-energy extraction from a HADDOCK PDB.
- Unit tests for dependency scanning on synthetic CNS snippets.
- Phase 0 single scoring A-job witness using
  `integration_tests/golden_data/ab_ag_BHL.pdb` and
  `integration_tests/golden_data/ab_ag_BHL.psf` with `nemsteps = 0`.
- Phase 1 rigidbody A-job witness using the E2A/HPR prepared topology fixtures
  from `tests/golden_data/` with `cmrest = true` and no AIR/restraint files.
- Phase 2 rigidbody A-module witness modelled on
  `examples/docking-protein-protein/docking-protein-protein-test.cfg`, but with
  `cmrest = true` replacing `data/e2a-hpr_air.tbl` and `sampling = 100`.
- Reproducibility analysis test or developer script, initially non-gating.
- Seamless CNS integration test, skipped unless `seamless-run` is available.
- Dependency identity regression test showing that changing an included `.cns`
  file changes the dependency sidecar identity.

## Acceptance Criteria

The pilot is successful when:

- The Phase 0 single-scoring test fails on meaningful HADDOCK score or
  unweighted-energy drift with `nemsteps = 0`.
- The Phase 0 single-scoring test fails on meaningful RMSD drift between input
  and scored output.
- A rigidbody A-job test fails on meaningful HADDOCK score drift.
- The same A-job test fails on meaningful RMSD drift.
- A rigidbody A-module test verifies PDB remarks, `PDBFile`, and `io.json`
  witnesses agree for all 100 generated outputs.
- The Phase 2 A-module test fails on R3 ensemble-quality drift when fewer than
  10 generated structures have HADDOCK score as good as the reference run's
  tenth-best score.
- The Phase 2 A-module test fails on R2 reproducibility drift when the
  score-sorted RMSD-to-reference-best table leaves its tolerance band.
- Baselines separate dependency, artifact, and witness layers.
- R1/R2/R3 are represented as reusable gate profiles over those layers.
- A-module tests rely on sidecars or deep indexes rather than committed output
  trees.
- CNS artifact reproducibility is explicitly classified before bitwise gates are
  made mandatory.
- `mode = "seamless"` can run the rigidbody pilot through `seamless-run` when
  `seamless-suite` is installed and the user has configured `SEAMLESS_CACHE`.

## Assumptions

- The pilot starts with single CNS scoring and then rigidbody minimization
  because both are CNS-backed, scientifically meaningful, and already have
  integration-test scaffolding.
- Witness-band gating is the default until artifact reproducibility analysis
  justifies stronger gates.
- Whole PDB goldens are acceptable for small A-job/A-body tests.
- A-module tests should prefer sidecars, deep indexes, and distribution
  witnesses.
- The pilot should seed the final system instead of being replaced by it.
