# Review: does `bitwise-reproducibility` deliver stage 1?

Independent review of branch `bitwise-reproducibility` (commits `699cfb7b2` "Normalize
CNS output artifacts reproducibly" and `ccd5f0980` "Add canonical CNS input
representation") against the stage-1 goal:

> Separate everything needed so CNS jobs have stable canonical inputs/outputs under
> irrelevant changes: run/step/install path, input filename, output filename, structure
> index where it is only a locator, etc. This is not "preserve implementation behavior";
> it is making the contract's identity model possible.

**Method.** Findings below were checked empirically, not only by reading: real `haddock3`
runs with CNS on a small two-chain system (`2oob_A` / `2oob_B`), run in pairs that differ
only in run-dir name, absolute path depth, input molecule filenames and `ncores`; plus
direct probes of `build_canonical_mapping` against the `.inp` files those runs generated.

---

## Verdict

**Partially.** The input-side canonicalization is real and works on genuine HADDOCK3 `.inp`
files. The output-side normalization is incomplete in exactly the way that defeats the
stage's purpose: two of the items named in the stage-1 brief — *input filename* and
*output filename* — still change the artifact bytes. So the branch does not yet deliver
stage 1.

---

## What is genuinely achieved (verified)

Two runs of the same workflow, differing in run-dir name, absolute path depth, input
molecule filenames, and `ncores`:

- **rigidbody output PDBs were bitwise identical.** The
  `REMARK initial structure N - ../0_topoaa/molA_haddock.pdb` line — which embeds the
  upstream step folder *and* the input filename — is correctly stripped. That's the single
  biggest win on the branch.
- **Canonical scripts are invariant to run dir, path depth, step ordinal**
  (`0_topoaa` → `00_topoaa`), **and install path.** I confirmed install-path invariance by
  pointing `MODULE`/`TOPPAR` at a copied tree under a different root: identical script,
  identical checksums.
- **The dependency scan works on real inputs** — 85 files resolved for rigidbody, 99 for
  flexref, 28 for topoaa, with sane `module/…` / `toppar/…` naming.
- **`sampling`, `$structures`, `$ini_count` are gone from the rigidbody `.inp`** (confirmed
  in the generated file), and seeds are `iniseed + idx`, correctly inherited downstream
  (emref jobs received rigidbody's 918/919).
- Orchestration parameters demonstrably don't reach the science: a run with
  `debug=true, ncores=2` produced byte-identical outputs to one with `debug` off — so the
  leak in gap 5 costs time, not correctness.

---

## Blocking gaps

### 1. Output filename is baked into every sampling/refinement/scoring PDB

`src/haddock/modules/sampling/rigidbody/cns/print_coorheader.cns:257` emits
`remarks HADDOCK stats for $output_pdb_filename`, and the normalizer doesn't strip it. The
same line exists in flexref, emref, mdref, emscoring, mdscoring.

Direct probe — the *same* `.inp` run twice, changing only the output filename literal
(`count` held at 1):

```
raw bytes identical:            False
AFTER normalization identical:  False
is_normalized_cns_pdb a/b:      True True
remaining differing lines:      2
    -REMARK HADDOCK stats for rigidbody_1.pdb
    +REMARK HADDOCK stats for rigidbody_9.pdb
```

A bit-identical computation yields different artifacts, and the normalizer certifies both
as clean. Note the sibling leak nine lines away (`initial structure`) *was* handled — one
of two was missed.

This cascades: emref reads `rigidbody_1.pdb`, so the upstream output filename enters the
downstream job's key. Rank invariance (the headline reuse case) cannot work until this is
fixed.

### 2. PSF title embeds its own filename

`src/haddock/libs/libcnsoutput.py:18-21` strips only the `DATE:` line; CNS also writes
`; FILENAME="molA_haddock.psf"`. Renaming an input molecule changed both topoaa PSFs, which
changed `canonical-input-1.psf` / `canonical-input-2.psf` checksums in the downstream
rigidbody key. `is_normalized_cns_psf` returns `True` for both.

### 3. Pin assignment is sorted-filename order, not binding order

`scan_cns_dependencies` returns `sorted(read_files)` and
`src/haddock/libs/libcnscanonical.py:446` numbers `canonical-input-{n}` by iterating that.
Its docstring says "first-reference order" — it isn't.

Probe: inputs renamed so alphabetical order reverses, content byte-identical:

```
canonical script identical: False
    -eval ($input_pdb_filename_1="canonical-input-1.pdb")
    +eval ($input_pdb_filename_1="canonical-input-2.pdb")
  pin canonical-input-1.pdb: content checksum changed 3a537c1d -> e6896654
  pin canonical-input-2.pdb: content checksum changed e6896654 -> 3a537c1d
```

The pins swapped because of a filename. This is precisely the "name → pin mapping must be
invariant" property, and it fails.

### 4. The canonicalizer hard-fails on three job shapes

| shape                       | result                                                              |
| --------------------------- | ------------------------------------------------------------------- |
| topoaa, rigidbody, flexref  | OK (run)                                                            |
| emref                       | `ValueError: unresolved reads: /protein-ss-restraints-all.cns` (run) |
| mdref                       | `ValueError: unresolved reads: …/mdref/cns/TOPPAR/dmso.pdb` (run)    |
| mdscoring                   | same `@@TOPPAR/` construct (static)                                 |
| topocg, cgtoaa, emscoring   | not reached; no known blocker                                       |

Two distinct causes:

- **emref**: `src/haddock/modules/refinement/emref/cns/emref.cns:236` writes
  `@MODULE:/protein-ss-restraints-all.cns`. `_resolve_reference` does
  `module_dir / token.split(":",1)[1]`, and pathlib discards the base when the operand is
  absolute. One-line fix (`.lstrip("/")`) — I patched it and emref then canonicalized
  cleanly with 34 deps, the two jobs differing only in `$seed`.
- **mdref/mdscoring**: `src/haddock/modules/refinement/mdref/cns/generate_dmso.cns:85` uses
  `@@TOPPAR/dmso.pdb` — a slash where CNS wants a colon. That's a latent bug in the CNS
  script, and the scanner refusing it is arguably correct behaviour (taxonomy 0b.9). Either
  way, mdref is uncanonicalizable today.

### 5. Orchestration parameters leak into every `.inp`

The generated rigidbody input carries `ncores`, `max_cpus`, `mode`, `batch_type`,
`queue_limit`, `concat`, `self_contained`, `clean`, `offline`, `debug`. Canonical scripts
across the two runs differed by exactly one line: `eval ($ncores=2)` vs `=3`.

Only `sampling`, and only in rigidbody, was addressed — `sampling_factor` still reaches
flexref/emref/mdref/cgtoaa. Also, `_cns_default_params`
(`src/haddock/modules/sampling/rigidbody/__init__.py:100`) uses a deny-list
(`dict(self.params)` then `pop`), which is the construction the taxonomy explicitly rules
out in favour of explicit inclusion, on the grounds that new orchestration params then leak
by default.

### 6. Batch/HPC and grid modes never normalize

`src/haddock/libs/libhpc.py:100` writes `{cns_exec} < {input_file} > {output_file}` into a
shell script; `CNSJob.run()` — and therefore `normalize_outputs()` — is bypassed entirely.
`libgrid` likewise packages jobs without calling `run()`. MPI is fine
(`src/haddock/clis/cli_mpi.py` calls `job.run()`). So artifacts are execution-mode
dependent.

---

## Scope concerns

**The rigidbody scheduling rewrite is a science change, not a reproducibility change.**
`_sample_models_to_dock` is genuinely prefix-stable and the old `sampling_factor` scheme
genuinely wasn't — it probably belongs here. But it changes user-visible results in two
ways, with no CHANGELOG entry, no docs, and no test:

```
sampling=1000 combinations=3:  old jobs=999   new jobs=1000
sampling=  10 combinations=4:  old jobs=  8   new jobs=  10
sampling=   5 combinations=4:  old jobs=  4   new jobs=   5
```

It also changes which ambig file pairs with which combination (combination-major →
round-robin). This is the only thing on the branch that alters results, and it's the only
thing untested. It deserves an explicit call-out to reviewers.

**All of commit 2 is dead code.** `canonical_mapping()` has no caller outside tests, so the
failures in gap 4 could never surface in a run. And `write_cns_dependencies` / the
`CNS_DEPENDENCIES` manifest is stage-2 cache machinery that doesn't belong in a
bitwise-reproducibility commit at all.

**The tests are the reason gap 4 went unnoticed.** Every case in
`tests/test_libcnscanonical.py` is a hand-written 3–5 line toy CNS script. Nothing
exercises a real generated `.inp`. A single test that canonicalizes one committed real
input per shape would have caught emref and mdref immediately.

---

## Smaller things

- `_canonicalize_locator_count_assignment` erases `$count` globally. It happens to be safe
  today (rigidbody uses it only in a warning `display`; refinement uses it as a locator for
  a restraint file that *is* resolved into the read-set) — but the taxonomy calls for a
  per-module verdict, and nothing guards this.
- `src/haddock/libs/libcnsoutput.py:55` does `.decode("utf-8")` strictly on scientific
  output, unguarded, after a successful CNS run — one non-UTF-8 byte fails the job at
  normalization time. It also rejoins with `\n` and splits on `str.splitlines()`, which
  treats `\x0b`, `\x0c`, `\x85`, ` ` as line breaks.
- `check_combination_chains` now runs `sampling` times instead of once per combination —
  O(sampling) redundant PDB parses on the default local path.
- `sampling_factor` is now always `1` at both call sites; the parameter is vestigial.
- `output_files` / `output_pdb_files` are two parameters for one concept, merged in the
  constructor and re-merged in `build_canonical_mapping`.
- `_rewrite_canonical_script` does a blind `str.replace` of bare basenames over the whole
  script. No collisions in the inputs checked, but two deps sharing a basename would
  silently mis-name (last replace finds nothing).
- `is_normalized_cns_artifact` is unused.

---

## Summary

Commit 1 solves the *step-path* half of output reproducibility and misses the *filename*
half, in both PDB and PSF. Commit 2's canonicalization is sound where it runs, and where it
runs it genuinely erases run dir, step ordinal, install path, and input paths — but it runs
on 3 of 8 shapes, is filename-order-dependent in its pin assignment, and still carries
`ncores` into the key.

The path to a real stage 1 looks short:

1. Strip `HADDOCK stats for` from PDB normalization and `FILENAME=` from PSF normalization.
2. Fix `@MODULE:/` resolution; fix or exclude `@@TOPPAR/dmso.pdb`.
3. Make pin assignment binding-derived rather than sort-derived.
4. Switch the CNS parameter set to explicit inclusion, across all CNS modules.
5. Move normalization somewhere batch/grid mode also reaches.

Each of those is small; together they're the difference between "the contract's identity
model is possible" and "not yet".
