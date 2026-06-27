# CNS Artifact Reproducibility Analysis

This document records Phase 3 of the CNS witness and Seamless pilot: whether
the Phase 1 rigidbody CNS A-job artifacts are raw-bitwise reproducible,
normalized-bitwise reproducible, or should remain witness-only.

## Scope

The analysis uses Phase 1-style rigidbody A-jobs derived from
`integration_tests/test_witness_rigidbody.py`.

Two cases are covered:

- `cmrest`: the original Phase 1 center-of-mass restraint case.
- `air_random_removal`: the same E2A/HPR prepared protein-protein topology
  fixtures, using `examples/docking-protein-protein/data/e2a-hpr_air.tbl`,
  `randremoval = true`, and `npart = 2`.

Both cases use `sampling = 1`, `ntrials = 1`, `iniseed = 917`; the first CNS job
therefore uses seed `918`. Each case produces one generated CNS input
(`rigidbody_1.inp`), one coordinate artifact (`rigidbody_1.pdb`), and CNS stdout
captured as `rigidbody_1.out`.

The AIR case tests random removal of ambiguous restraints from a supplied AIR
file. It is not the `ranair` mode that randomly defines AIRs from surface
patches.

Phase 2 module-level ensemble reproducibility was not rerun here. Phase 3 is
deliberately centered on the smaller A-job because it is the cache unit proposed
for the initial Seamless CNS mode.

## Tooling

The analysis is captured in:

```bash
python devtools/analyze_cns_reproducibility.py --runs 3
```

The tool runs each selected Phase 1 A-job case in fresh `run_*/` directories and
compares the generated artifacts by raw checksum and by an exploratory
normalized checksum. By default it runs both cases; pass `--case cmrest` or
`--case air_random_removal` to run only one. By default it leaves the temporary
run directories in place for manual inspection; pass `--cleanup` to remove them
after the JSON summary is printed.

The normalizers tested here are intentionally small:

- PDB: reuse `normalize_cns_pdb_for_checksum`, which removes CNS volatile
  `REMARK initial structure ...` and `REMARK DATE:` lines.
- Text artifacts: normalize line endings, replace fresh `../run_N` path
  fragments, and normalize CNS stdout start/stop timestamps and CPU time.

## Evidence

Commands run during this phase:

```bash
python -m pip install -e .
python -m pytest integration_tests/test_witness_rigidbody.py::test_rigidbody_ajob_witness --witness-regime=R1 -q
python devtools/analyze_cns_reproducibility.py --runs 3 --cleanup
```

The R1 Phase 1 witness test passed. The three-run reproducibility analysis also
reported identical witnesses within each case.

Center-of-mass case witnesses:

| witness | value |
| --- | ---: |
| HADDOCK score | `11.679701999999999` |
| RMSD to committed reference | `5.317585669890707e-15` |
| unweighted vdw | `-21.8351` |
| unweighted elec | `1.96685` |
| unweighted desolv | `16.3356` |
| AIR/CNS NOE energy | `74.9243` |
| AIR/CNS NOE rms-dev | `15.8543` |
| AIR/CNS NOE violations | `1` |
| AIR CV partition count | `0` |

AIR random-removal case witnesses:

| witness | value |
| --- | ---: |
| HADDOCK score | `-23.2601172` |
| RMSD to first AIR run | `5.920465772752098e-15` |
| unweighted vdw | `6.23928` |
| unweighted elec | `-7.92863` |
| unweighted desolv | `-6.24696` |
| AIR energy | `195.598` |
| AIR rms-dev | `0.549338` |
| AIR violations | `6` |
| AIR CV partition count | `2` |
| AIR CV violations | `5` |
| AIR CV rms | `0.586461` |

Artifact classification from the three fresh center-of-mass runs:

| artifact | raw bytes | normalized checksum | classification |
| --- | --- | --- | --- |
| `rigidbody_1.inp` | differ | equal | normalized-bitwise stable |
| `rigidbody_1.pdb` | differ | equal | normalized-bitwise stable |
| `rigidbody_1.out` | differ | equal | normalized-bitwise stable |
| `rigidbody_1.cnserr` | absent | absent | absent/conditional |
| `rigidbody_1.seed` | absent | absent | absent/conditional |

Artifact classification from the three fresh AIR random-removal runs:

| artifact | raw bytes | normalized checksum | classification |
| --- | --- | --- | --- |
| `rigidbody_1.inp` | differ | equal | normalized-bitwise stable |
| `rigidbody_1.pdb` | differ | equal | normalized-bitwise stable |
| `rigidbody_1.out` | differ | equal | normalized-bitwise stable |
| `rigidbody_1.cnserr` | absent | absent | absent/conditional |
| `rigidbody_1.seed` | absent | absent | absent/conditional |

Raw differences were explained by volatile but non-scientific content:

- `rigidbody_1.inp`: relative fresh run directory paths such as
  `../run_1/...` vs `../run_2/...`.
- `rigidbody_1.pdb`: `REMARK initial structure ...` path lines and CNS
  `REMARK DATE:...` lines.
- `rigidbody_1.out`: CNS program start/stop timestamps, CPU time, and echoed
  input paths from the generated `.inp`.

## Source Inspection

The relevant execution path is `CNSJob.run` in
`src/haddock/libs/libsubprocess.py`. For path-backed input files it starts the
CNS executable, passes the generated `.inp` as stdin, captures CNS stdout as raw
bytes, and writes those bytes directly to the configured `.out` file. HADDOCK3
does not currently normalize `.out` while writing it.

The Phase 1 fixture drives `src/haddock/modules/sampling/rigidbody/__init__.py`.
`prepare_cns_input_sequential` derives the CNS seed as `iniseed + idx`, so the
first generated input uses `918` for this pilot. `make_cns_jobs` creates the
corresponding `CNSJob` and expected `rigidbody_1.pdb`.

The rigidbody CNS script sets the seed and writes coordinates through:

```text
set seed $seed end
write coordinates format=pdbo output=$output_pdb_filename end
```

For supplied AIRs with random removal, `rigidbody.cns` sets:

```text
evaluate ($data.randremoval=$randremoval)
evaluate ($data.npart=$npart)
set seed $seed end
inline @MODULE:read_noes.cns
if ($Data.randremoval eq true) then
    noe cv $npart ? end
end if
```

The seed therefore controls both rigidbody randomization and the cross-validated
partitioning/removal path for this A-job. In the tested AIR case, the generated
PDB records `AIRs cross-validation: 2, 5, 0.586461` identically across three
fresh runs.

The PDB header is produced via
`src/haddock/modules/sampling/rigidbody/cns/print_coorheader.cns`. That script
records the HADDOCK score, unweighted energies, AIR violations, AIR
cross-validation witnesses, and the `$input_pdb_filename_*` values. CNS itself
adds the `REMARK DATE:` line during coordinate writing.

## Module Handoff Semantics

In the current HADDOCK3 module structure, generated `.inp` and `.out` files are
not treated as normal output artifacts passed to the next module. The regular
workflow handoff is the module `io.json`, and that file is produced from
`self.output_models` through `BaseHaddockModule.export_io_models`.

The relevant source pattern is:

- `BaseHaddockModule.export_io_models` writes `self.previous_io.output` as the
  module input list and `self.output_models` as the module output list.
- `ModuleIO.retrieve_models` reconstructs the next module's model stream from
  `previous_io.output`, keeping entries whose `file_type` is `Format.PDB` and
  nested topology/ensemble dictionaries whose values are PDB models.
- CNS modules create `.inp` and `.out` filenames for `CNSJob`, but append only
  expected PDB objects to `self.output_models`.
- `prepare_cns_input` writes the CNS variable `output_pdb_filename` as a
  `*.pdb` name, while `prepare_expected_pdb` creates the corresponding
  `PDBFile` object that becomes the module output.

The inspected CNS-backed modules follow this convention:

- `topoaa` and `topocg` execute CNS topology jobs with `.out` logs, then export
  dictionaries of `PDBFile` objects with attached `TopologyFile` metadata.
- `rigidbody`, `flexref`, `emref`, `mdref`, `cgtoaa`, `emscoring`, and
  `mdscoring` create `CNSJob(input, *.out, *.cnserr)` tasks, but append only
  `prepare_expected_pdb(...)` results to `self.output_models`.
- Analysis and non-CNS scoring modules either pass through prior `PDBFile`
  objects or create new `PDBFile` objects. Worker `.out` files and tabular
  outputs are diagnostics/results, not the main next-module model stream.

There is one intentional side-channel: `rmsdmatrix` and `ilrmsdmatrix` write a
separate `rmsd_matrix.json` containing an `RMSDFile`, and clustering modules
load that sidecar explicitly. This does not alter the regular `io.json` chain,
which still passes the PDB models onward.

This is an implementation invariant rather than a type-system guarantee:
`ModuleIO.add` is permissive and can store arbitrary objects. Current module
implementations nevertheless keep `.inp` and `.out` as execution/dependency or
diagnostic files. For Seamless CNS wrapping, this means `.inp` should be treated
as part of the dependency identity, `.out` as a diagnostic transcript, and the
generated PDB plus parsed witnesses as the scientific handoff surface.

## CNS Restraint Reproducibility Inventory

This inventory is limited to HADDOCK3's CNS-backed modules. OpenMM constraints
and LightDock active/passive inputs are real workflow controls, but they are not
`.cns` restraint definitions and are outside this CNS replacement analysis.

The current CNS restraint surface falls into distinct reproducibility classes:

| restraint family | entry points | source-level behavior | reproducibility assessment |
| --- | --- | --- | --- |
| Supplied ambiguous distance restraints | `ambig_fname` in `rigidbody`, `flexref`, `emref`, `mdref` | `read_noes.cns` / `read_data.cns` reads `noe class ambi` from the configured file, with model-indexed filenames tried first. | Strongly reproducible if file bytes, model index, and path resolution are fixed. |
| Supplied unambiguous distance restraints | `unambig_fname` in `rigidbody`, `flexref`, `emref`, `mdref` | CNS reads `noe class dist` from the configured file. | Strongly reproducible if file bytes and path resolution are fixed. |
| Supplied hydrogen-bond restraints | `hbond_fname` in `rigidbody`, `flexref`, `emref`, `mdref` | CNS reads `noe class hbon` from the configured file. | Strongly reproducible if file bytes and path resolution are fixed. |
| Random removal of supplied AIRs | `randremoval`, `npart` | After reading AIRs, CNS runs `noe part $Data.npart`; later the protocol queries `noe cv $npart`. The module sets `seed` before this path. | Reproducible only as deterministic-given-seed. The selected partition and CV witnesses are part of the scientific output surface. |
| Random interaction restraints | `ranair`, `nrair_*`, `rair_sta_*`, `rair_end_*` in `rigidbody` | `randomairs.cns` computes solvent accessibility, chooses residues with `ran()`, builds ambiguous `noe` assignments, and writes a `.disp` diagnostic file. `rigidbody.cns` disables `randremoval` when `ranair` is true. | More fragile than random removal. Reproducible only if seed, input coordinates, atom/residue order, surface-accessibility calculation, molecule count, and allowed residue ranges are fixed. The generated restraint selection or `.disp` file should be treated as a witness/artifact. |
| Center-of-mass restraints | `cmrest`, `cmtight`, `kcm` in `rigidbody`, `flexref`, `mdref` | `cm-restraints.cns` deterministically builds center-averaged distance restraints between molecules from current coordinates and component metadata. | Reproducible from fixed coordinates, atom selections, molecule order, and config. No CNS RNG calls. |
| Surface contact restraints | `surfrest`, `ksurf` in `rigidbody` | `surf-restraints.cns` deterministically creates contact restraints between molecular surfaces. | Reproducible from fixed coordinates, selections, and molecule order. No CNS RNG calls. |
| Interface contact AIRs | `contactairs`, `kcont` in `flexref`, `emref`, `mdref`, `mdscoring` | `contactairs.cns` deterministically generates ambiguous distance restraints from the current interfacial contacts. | Reproducible from fixed coordinates, atom/residue order, and cutoff behavior. It is generated, so the dependency surface includes the input structure, not only a restraint file. |
| User dihedral restraints | `dihe_fname`, `dihedrals_on` in `flexref`, `emref`, `mdref` | `read_data.cns` reads `restraints dihedral` from the configured file. | Strongly reproducible if file bytes and path resolution are fixed. |
| Automatic protein secondary-structure dihedrals | `ssdihed`, `error_dih` in `flexref`, `emref`, `mdref`, `mdscoring`; always used in parts of `cgtoaa` | `protein-ss-restraints-*.cns` uses CNS `pick dihedral ... geometry` over selected residues. | Reproducible from fixed coordinates, residue typing, atom order, and chosen `ssdihed` mode. No CNS RNG calls, but it is coordinate-derived. |
| DNA/RNA conformation restraints | `dnarest_on` in `flexref`, `emref`, `mdref`, `mdscoring` | `dna-rna_restraints.cns` creates base planarity, sugar-pucker, phosphate-backbone, and Watson-Crick restraints using deterministic CNS selections and geometry. | Reproducible from fixed nucleic-acid coordinates, residue naming, atom order, and config. No CNS RNG calls. |
| Symmetry restraints | `sym_on`, `symtbl_fname`, and symmetry-specific parameters | `symmultimer.cns` deterministically generates C2/C3/C4/C5/C6/S3 symmetry restraints, or reads a custom symmetry table if provided. | Reproducible from fixed config/table bytes, component order, and coordinates. Custom tables should be explicit dependencies. |
| NCS restraints | `ncs_on`, `kncs`, `nncs`, and NCS segment parameters in `flexref`, `emref`, `mdref` | The modules set `Data.flags.ncs` from config and include CNS `ncs` when enabled. Headers report NCS energy. | Reproducible in principle from fixed config and selections, but this pilot has not exercised an NCS fixture. |
| Ion coordination restraints | automatic `restrain-ions.cns` in CNS refinement/scoring modules | CNS creates distance restraints between ions and nearby coordinating atoms using deterministic distance cutoffs and `pick bond ... geometry`. | Reproducible from fixed coordinates and atom order, but cutoff/tie cases can be numerically brittle. |
| Covalent ion handling | `covalions.cns` in `flexref` | CNS picks the closest coordinating atom within a cutoff and adds a bond parameter. | Deterministic but more cutoff/tie sensitive than file-read restraints. Include ion-coordinate fixtures if replacing this path. |
| Protocol-internal harmonic/fixed-coordinate restraints | e.g. EM/refinement harmonic restraints and `mol_fix_origin_*`/shape handling | CNS applies deterministic harmonic restraints or fixed selections during minimization/refinement phases. | Reproducible from fixed selections and coordinates. These are not user evidence restraints, but they affect output coordinates and should be in the dependency model. |

Several scoring/header terms look like restraint families but are not active
restraint mechanisms in the inspected CNS modules. `w_sani`, `w_xrdc`,
`w_xpcs`, `w_dani`, `w_vean`, `w_rg`, `w_zres`, and `w_lcc` exist in defaults
or headers. The rigidbody, flexref, mdref, mdscoring, and emscoring entrypoint
scripts explicitly set the corresponding `Data.flags.*` values to false; emref
has the same print-header conditionals but no activating parameter path was
found in this inspection. These terms should not be treated as covered current
restraint types until a fixture activates real CNS behavior for them.

The main conclusion is that the restraint types are not equally reproducible.
They can be ranked from the point of view of a Seamless/CNS replacement gate:

1. File-read restraints are the cleanest cache dependencies: the restraint file
   bytes are the dependency, and the resulting score/header witnesses should be
   stable.
2. Deterministically generated restraints are reproducible, but the generator
   script, coordinates, atom/residue order, component order, and relevant
   cutoffs are dependencies.
3. Seeded stochastic restraints, especially `ranair`, require more than final
   score checks. The seed, RNG call sequence, model index, selected/generated
   restraints, AIR energies, AIR violations, and any `.disp` diagnostic output
   should be witnessed or compared.
4. Dormant score/header terms should stay outside the initial CNS replacement
   gate unless a real activating fixture is added.

For the pilot, the existing AIR random-removal case covers stochastic
partitioning of supplied AIRs, but it does not cover `randomairs.cns`. A
handoff-ready next extension would add a small protein-protein `ranair` case and
compare the generated restraint selection or `.disp` file, AIR/CV witnesses,
HADDOCK score, and normalized output PDB.

## Conclusions

For both Phase 1 A-job cases, scientific witnesses are stable, and the core
generated artifacts can be made normalized-bitwise stable with narrow,
explainable normalization. Raw byte identity is not currently true across fresh
run directories.

The AIR/random-removal extension does not change the artifact classification:
the same categories of volatility appear, and no additional stochastic drift was
observed when the CNS seed and AIR input were fixed. It does, however, expand
the relevant witness set. A CNS replacement should not only reproduce the final
HADDOCK score and coordinates; it should also reproduce the AIR energy,
violations, cross-validation partition count, cross-validation violations, and
cross-validation RMS.

Recommended gate use:

- `rigidbody_1.pdb`: suitable for an R1 normalized-bitwise checksum gate using
  the current CNS PDB normalizer, plus tight witness checks.
- `rigidbody_1.inp`: suitable for dependency identity only after canonicalizing
  or normalizing generated input paths. It should be treated as part of the
  dependency layer, not as a scientific output artifact.
- `rigidbody_1.out`: useful as a diagnostic artifact and potentially
  normalized-checksum stable, but should not be a primary R1 scientific gate.
  It is a CNS transcript with timestamps, CPU time, and echoed input paths.
- `.cnserr` and `.seed`: conditional artifacts in this fixture. They should be
  asserted absent on successful runs or handled only when the execution path
  actually produces them.

For a CNS replacement pilot, the immediate practical gate should be:

1. exact or normalized dependency identity for the generated `.inp`;
2. normalized checksum identity for the output PDB;
3. tight witness gates for HADDOCK score, unweighted energies, RMSD, and
   AIR-specific values when AIRs/random removal are enabled.

This is strong enough to catch score/header/coordinate regressions while not
mistaking timestamps or fresh-directory names for scientific differences.

## Caveats

The result is local evidence from the bundled Linux CNS executable in this
checkout. It does not yet prove identical normalized behavior across operating
systems, compilers, CPU architectures, MPI/parallel execution modes, other
`npart` values, other AIR files, `ranair` generated restraints, or other
CNS-backed modules such as `flexref`, `emref`, `mdref`, or topology generation.

The `dirac-proxy-info` warnings printed during the tool run came from importing
the integration-test package and were not part of CNS execution or artifact
comparison.
