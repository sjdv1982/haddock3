# Review 2: does `bitwise-reproducibility` deliver stage 1?

Independent review of the second iteration of branch `bitwise-reproducibility`
(`ea89942a7..ac144fc7d`: `699cfb7b2` + fixup `ab3219aaf`, `ccd5f0980` + fixup
`ac144fc7d`), against the stage-1 goal:

> Separate everything needed so CNS jobs have stable canonical inputs/outputs under
> irrelevant changes: run/step/install path, input filename, output filename, structure
> index where it is only a locator, etc. This is not "preserve implementation behavior";
> it is making the contract's identity model possible.

**What was reviewed.** The tree at `ac144fc7d`, the branch tip. The working copy is
mid-rebase squashing the two fixups; that is cosmetic, so the branch tip is the content.

**Method.** Everything below was produced empirically with the real CNS 1.3U binary and
real `haddock3` runs, not by reading alone:

- a probe wrapping `CNSJob.run` that builds each job's `canonical_mapping()` and records
  the canonical script, pin table and checksum tree — run over all nine CNS job shapes;
- paired all-atom runs (topoaa → rigidbody → flexref → emref → mdref → emscoring →
  mdscoring) differing only in run-dir name, absolute path depth, input molecule
  filenames (chosen so alphabetical order *reverses*) and `ncores`;
- a third run of the same workflow against a HADDOCK3 install relocated to a different
  root;
- a 2-step vs 12-step pair, to cross the zero-fill boundary (`0_topoaa` → `00_topoaa`);
- a direct CNS probe running one real `rigidbody_1.inp` twice with only the output
  filename literal changed;
- coarse-grained runs (topoaa → topocg → rigidbody → cgtoaa → emref) and repeated
  identical CG runs;
- adjacent MUST-MISS probes (molecule swap, seed, `tolerance`);
- a direct probe of `libparallel.Scheduler` result ordering, and a rigidbody run with
  injected scheduling skew.

---

## Verdict

**Not yet — but the distance left is short and specific, and the identity model itself
now works.**

Iteration 1's gaps were omissions: things not stripped, shapes not handled. **All six of
them are genuinely fixed, and I verified each fix rather than taking it on trust.** On
the perturbations stage 1 names — run dir, step ordinal, path depth, input filename,
output filename, structure index — an all-atom workflow now produces **16 of 16
bitwise-identical artifacts and 14 of 14 identical job keys**, and correctly *misses*
when a binding changes.

What remains is five defects, each demonstrated:

1. Output normalization is **racy** on the default local path; under scheduling skew half
   the rigidbody artifacts are left un-normalized.
2. Both topology modules embed **62 absolute install paths** in their canonical scripts.
3. `topocg`'s **output binding is silently corrupted** by a basename collision, and the
   completeness guard passes it.
4. `topocg`'s output is **nondeterministic run to run** — unseeded `random` in
   `libaa2cg.py` — so the whole coarse-grained branch is unreproducible for reasons
   normalization cannot reach.
5. The resolver **rejects two reachable configurations**: `cgtoaa`, and any module
   given a `.tgz` restraint archive. Neither is intrinsically uncanonicalizable; both
   are gaps in the resolver.

None of these is visible to the branch's own tests, for the same reason as in iteration
1: every canonicalization fixture is a hand-written toy script.

---

## Confirmed fixed (verified, not assumed)

| Iteration-1 gap | Status |
|---|---|
| 1. `HADDOCK stats for` baked into every PDB | **Fixed.** Direct CNS probe: one real `rigidbody_1.inp`, run twice, output renamed to `zzz_999_totally_other.pdb`. Raw bytes differ; **normalized bytes identical**. |
| 2. PSF title embeds its own filename | **Fixed.** `; FILENAME=` is rewritten to a fixed canonical string. Renaming both input molecules left all four topoaa PSFs byte-identical. |
| 3. Pin assignment was sorted-filename order | **Fixed.** `scan_cns_dependencies` returns first-reference order. Verified with `molA→zz_one`, `molB→aa_two` (alphabetical order reversed): pins and checksums unchanged. |
| 4. Canonicalizer hard-failed on emref / mdref / mdscoring | **Fixed** (`MODULE:/` `.lstrip("/")`, plus `MODULE/` and `TOPPAR/` branches). All three canonicalize on real inputs; `topocg` and `emscoring` too. |
| 5. Orchestration parameters in every `.inp` | **Mostly fixed.** All twelve globals (`ncores`, `max_cpus`, `mode`, `batch_type`, `queue`, `queue_limit`, `concat`, `self_contained`, `clean`, `offline`, `debug`, `cns_exec`) plus `sampling` / `sampling_factor` are gone from the generated `.inp`. `tolerance` remains — see below. |
| 6. Batch/HPC and grid never normalized | **Fixed in code** (`HPCWorker.normalize_outputs`, `GridInterface._normalize_output`), with unit tests. No SLURM or DIRAC here, so this one is read-and-reasoned, not measured. |
| `.decode("utf-8")` + `str.splitlines()` on scientific output | **Fixed.** Normalization is byte-level and splits on LF only. |

### Verified end-to-end

Two runs of a seven-module all-atom workflow, differing in run-dir name, absolute path
depth, both input molecule filenames and `ncores` (2 vs 4):

```
canonical script + pin checksums:  IDENTICAL for all 14 jobs
                                   (topoaa ×2, rigidbody ×2, flexref ×2, emref ×2,
                                    mdref ×2, emscoring ×2, mdscoring ×2)
output artifacts:                  SAME for all 16 .pdb/.psf files
```

A third run against a **relocated install** produced the same 16 artifacts byte-for-byte
(the install path leaks into *keys*, not into outputs — B2).

Also verified:

- **Zero-fill boundary (Axis 2.5).** 2-step run vs 12-step run, so `0_topoaa` becomes
  `00_topoaa`: canonical forms and artifacts identical for both retained steps.
- **Ambient process state (Axis 8.6).** `TZ=Asia/Tokyo`, `LC_ALL=C`, `LANG=C`,
  `PYTHONHASHSEED=12345`: artifacts identical.
- **Normalization completeness on the happy path.** Every `.pdb`/`.psf` in two completed
  runs — including topocg's `gen_cg_filename` outputs — passes `is_normalized_cns_*`.
- **MUST-MISS adjacency (Axis 0b.1).** Swapping the two molecules in `molecules = [...]`
  changes `canonical-input-{1,2}.{pdb,psf}` and the canonical script. The key is not
  merely over-invariant.
- **Seed provenance.** Two flexref jobs differ in exactly one canonical line,
  `eval ($seed=918)` vs `919`, inherited from their rigidbody parents.
- **`$structures` / `$ini_count` removal is safe.** Neither symbol is referenced anywhere
  in the rigidbody CNS tree (or, for `$structures`, anywhere at all).
- **`$count` erasure is justified today.** Its only uses across all CNS templates are one
  `display` in `rigidbody.cns` and `$ambig_fname + "_" + encode($count)`, a locator for a
  file the scanner resolves into the read-set by content. Correct — but still global and
  unguarded, with no per-module verdict recorded.

Canonical scripts contain zero run-directory tokens and zero step-folder tokens across
every shape that canonicalizes. That half of taxonomy 0b.10 is genuinely discharged.

---

## Blocking defects

### B1. Output normalization is racy — artifacts are not reproducible on the default path

`libparallel.Scheduler` collects results in **worker-completion order**. Each `Worker`
puts its whole chunk on a queue as it finishes, and `Scheduler.run` flattens
`all_results` in arrival order (`libparallel.py:190-193`). Direct probe, 8 tasks / 4
workers with the first chunk made slow:

```
submission order : [0, 1, 2, 3, 4, 5, 6, 7]
Scheduler.results: [2, 3, 4, 5, 6, 7, 0, 1]
```

`rigidbody.prepare_cns_input_parallel` then does `zip(_l, prepare_engine.results)`,
pairing **submission-ordered** metadata with **completion-ordered** scripts. Real
`haddock3` run, `sampling = 8`, `ncores = 4`, with a delay injected into the first
chunk's `prepare_cns_input`:

```
declared output          script actually writes
rigidbody_1.pdb    <->   rigidbody_3.pdb
rigidbody_3.pdb    <->   rigidbody_5.pdb
rigidbody_5.pdb    <->   rigidbody_7.pdb
rigidbody_7.pdb    <->   rigidbody_1.pdb
...  (all 8 jobs mispaired)
```

Each job's `normalize_outputs()` therefore targets a file it did not write. Where that
file does not exist yet, `normalize_cns_pdb` returns `False` silently and nobody ever
normalizes the real output:

```
rigidbody_1.pdb  normalized=False        rigidbody_5.pdb  normalized=False
rigidbody_2.pdb  normalized=False        rigidbody_6.pdb  normalized=False

REMARK FILENAME="rigidbody_1.pdb"
REMARK HADDOCK stats for rigidbody_1.pdb      <- exactly what stage 1 exists to remove
```

Four of eight artifacts left carrying volatile headers, on `mode = local`, in the module
with the highest job count.

Two things to separate:

- The `zip` and the completion-ordered `results` are **unchanged from the merge base**.
- But they were previously *harmless*: everything else in the tuple (combination, ambig,
  seed, and the `idx` used to name the expected PDB) comes from submission order and
  stays mutually consistent, and the script is self-describing. The branch adds the
  first consumer that requires the pairing to actually hold — `output_pdb_files` — and
  thereby promotes a latent bookkeeping bug into a stage-1 blocker.

Five unskewed runs at `sampling=8, ncores=4` showed no divergence, so this will not be
caught by luck; it needs load imbalance, which real systems and ensembles supply.

The fix belongs in `libparallel.Scheduler` — return results in submission order — not in
a workaround at the call site, since anything else that consumes `Scheduler.results`
positionally has the same bug.

### B2. `topoaa` and `topocg` canonical scripts embed absolute install paths

Same workflow, same everything, only the HADDOCK3 install relocated to a different root:

```
0_topoaa      canonical script DIFFERS  (62 absolute install paths)
1_topocg      canonical script DIFFERS  (62 absolute install paths)
2_rigidbody … 6_mdscoring                0 absolute install paths — all IDENTICAL
```

`generate_default_header` writes 62 path-valued assignments into every topology `.inp` —
51 `$trans_vector_N`, six tensor files, `$scatter_lib`, `$top_axis`, `$par_axis`,
`$top_axis_dani`, `$boxtyp20` — as absolute install paths. The topology recipes never
`@@`-read them, so they are not in the scanned read-set, so `_rewrite_canonical_script`
has no entry for them and they survive verbatim into the key.

This is Axis 1.3 becoming a silent false miss, on the one shape whose output feeds every
other shape. Either rewrite any path under `module_dir`/`toppar_dir` to its canonical
`toppar/…` spelling whether or not it is read, or stop emitting unreferenced path
variables into `.inp` files that never use them.

### B3. `topocg`'s output binding is corrupted by a basename collision

For a shape molecule, topocg reads `../0_topoaa/shape_haddock.pdb` and writes
`./shape_haddock.pdb` — different paths, **same basename**.
`_rewrite_canonical_script` includes bare `path.name` in its candidate set and processes
dependencies before outputs, so the input's rewrite consumes the output spelling:

```
declared outputs        = ['shape_haddock.pdb', 'shape_haddock.psf']
canonical_output_names  = ('canonical-output.pdb', 'canonical-output.psf')
script says             : eval ($output_pdb_filename="canonical-input-1.pdb")   <- wrong
input pin               : canonical-input-1.pdb <- 0_topoaa/shape_haddock.pdb
```

The mapping declares one output binding and the script performs another. Output shape is
part of the transformation (Axis 0b.5), so this is an identity defect, not cosmetics —
and if the canonical script were ever executed it would overwrite its own input. This is
the "two deps sharing a basename" hazard flagged in iteration 1, now demonstrated on a
real job.

**Related, and broader:** the alias substitution also destroys CNS variable *names*. In
every `prepare_single_input` shape (topoaa, topocg) the canonical script contains

```
eval (canonical-input-1.pdb="canonical-input-1.pdb")      ! was: eval ($file="…/molA.pdb")
evaluate($coor_infile= canonical-input-1.pdb)             ! was: evaluate($coor_infile= $file)
```

because `_script_dependency_aliases` records `"$file" → canonical-input-1.pdb` and
`_replace_script_token` then rewrites the assignment's left-hand side too. Identity-wise
this over-collapses (two distinct variables aliasing one file become one token); more
practically it makes the canonical form syntactically invalid CNS, which forecloses the
taxonomy's plan of dumping a job as a runnable `seamless-run` command and makes a golden
canonical form harder to review.

### B4. `topocg` output is nondeterministic — normalization cannot reach this

Two runs of a byte-identical config, same inputs, same filenames, `ncores = 1`:

```
2r15_A_haddock_cg_martini2.pdb   DIFF
2r15_B_haddock_cg_martini2.pdb   DIFF
```

Five pipeline runs produced five distinct normalized outputs. It is not a header leak —
the coordinates differ, in the `SCD*` dummy beads:

```
- ATOM      5 SCD1 SER A1462     -29.859 -14.171   8.823
+ ATOM      5 SCD1 SER A1462     -29.813 -14.290   8.862
```

Cause: `libs/libaa2cg.py:389-393` places dummy beads using a random vector drawn from the
**unseeded global `random` module**. Proven by construction — with `random.seed(0)` set
before the run, two runs produce byte-identical topocg outputs.

The generated `.inp` is identical between runs, and rerunning one `.inp` three times gives
identical normalized output, so CNS is not at fault; the nondeterminism is on the Python
side, upstream of CNS. It then propagates into every downstream CNS job in a CG workflow
(rigidbody, cgtoaa, emref all read the CG PDB), which is why the CG pair in my matrix
shows `DIFF` at every step after topocg while the all-atom pair shows `SAME` everywhere.

This is Axis 0, the precondition for every MUST-HIT in the taxonomy. It is pre-existing
and outside the branch's diff, but stage 1 cannot be declared done for the CG shapes
until it is fixed, and the fix is one line (seed the generator from `iniseed`, or draw
from a `RandomNumberGenerator` as the rest of the codebase does).

### B5. Two reachable configurations are rejected by the resolver

**`cgtoaa` — a resolver gap, not an intrinsic property:**

```
ValueError: Canonical CNS input has unresolved reads:
            $input_aa_pdb_filename_, $input_aa_psf_filename_, $input_cgtbl_filename_
```

`cgtoaa.cns` splices a loop variable into a *symbol name*:

```cns
while ($nchain < $data.ncomponents) loop nloop1
    evaluate ($nchain = $nchain + 1)
    if ($mol_shape_$nchain eq false) then
        if ($exist_input_aa_psf_filename_$nchain eq true) then
            structure @@$input_aa_psf_filename_$nchain end
            coor      @@$input_aa_pdb_filename_$nchain
```

**Everything that loop touches is statically declared in the same `.inp`** — the loop
bound, the guards and the operands alike:

```
eval ($ncomponents=3)
eval ($mol_shape_1=false)   eval ($mol_shape_2=false)   eval ($mol_shape_3=true)
eval ($input_aa_psf_filename_1="../0_topoaa/2r15_A_haddock.psf")
eval ($input_aa_pdb_filename_1="../0_topoaa/2r15_A_haddock.pdb")
eval ($input_cgtbl_filename_1="…/1_topocg/2r15_A_haddock_cg_to_aa.tbl")
eval ($input_aa_psf_filename_2=…)  …
```

`$nchain` is therefore job-invariant in the only sense that matters here: its range is
fixed by the script, and the read-set is a pure function of the script text with no
ambient or runtime input. **A cgtoaa job is canonicalizable in principle.** What fails is
`_resolve_reference`: `_REFERENCE_PATTERN` matches `[$&][A-Za-z0-9_]+`, so
`@@$input_aa_psf_filename_$nchain` is split into a token `$input_aa_psf_filename_`
(trailing underscore) and a separate `$nchain`; the prefix is not a defined variable, so
it resolves to `None` and is reported unresolved.

Hard-failing on something it cannot resolve is still the **correct** behaviour under Axis
0b.9 — the objection is not to the failure, it is that a supported job shape is excluded
by a resolver limitation that is cheap to lift. The library already carries machinery for
a *harder* dynamic form, `$base + "_" + encode($count)`
(`_COUNT_SUFFIX_REFERENCE_PATTERN`), which has to probe the filesystem. The splice form
needs no probing at all: expand `$prefix_$var` to every already-assigned symbol matching
`^prefix\d+$` and resolve each. Two things to decide when doing so:

- **Over-declaration.** The guards (`$mol_shape_N`, `$exist_…_N`) mean not every declared
  index is necessarily read. Declaring them all is safe in the direction that matters — a
  superset read-set can only cause false misses, never false hits — but it does put a
  file in the key that the job may not read.
- `$input_cgtbl_filename_N` is written as an **absolute run-directory path** where the
  psf/pdb are relative. That is not an extra obstacle (once it is a dependency,
  `_rewrite_canonical_script` has `str(path)` in its candidate set), but it is why the
  failure surfaces as "unresolved reads" rather than as a step-folder leak.

Either lift the limitation or declare the shape out of scope — but the declaration should
be a decision with a test, not the current state of affairs where it is a side effect of
a regex.

**Noticed while reading that `.inp`, and outside this branch:** `_add_cg_backmapping_arguments`
builds `aa_psf_list` over *all* molecules but `cgtoaa_tbl_list` over *non-shape* molecules
only, then `zip`s them. With the shape last the two indexings coincide. With the shape
first they do not — verified by running the shipped shape example with the molecule order
rotated:

```
input_aa_psf_filename_1 = shape_haddock.psf     input_cgtbl_filename_1 = 2r15_A…_cg_to_aa.tbl
input_aa_psf_filename_2 = 2r15_A_haddock.psf    input_cgtbl_filename_2 = 2r15_B…_cg_to_aa.tbl
                                                 2r15_B_haddock.psf dropped by zip
```

so molecule B is never back-mapped and A is restrained by B's restraints. Pre-existing,
unrelated to this branch, and worth a separate issue.

**Any module given a `.tgz` restraint archive** (`examples/docking-multiple-ambig`):

```
ValueError: Canonical CNS script leaked step-folder token '/1_rigidbody'
    line 5:   eval ($ambig_fname="../data/1_rigidbody/ambig.tbl.tgz")
    line 295: eval ($ambig_fname="../data/1_rigidbody/ambig_1.tbl")
```

`prepare_cns_input` assigns `$ambig_fname` twice: once from the raw config value (the
archive) and once with the per-job resolved `.tbl`. CNS uses the last assignment, and the
canonicalizer correctly pins `ambig_1.tbl` by content — but the **stale first
assignment** names a file that is not in the read-set, so nothing rewrites it and the
guard rejects the whole job. A single `ambig.tbl` is fine (both spellings are the same
dependency); the archive form is not. All three rigidbody jobs in that example fail.

Measured shape coverage: **8 of 9 canonicalize** (topoaa, topocg, rigidbody, flexref,
emref, mdref, emscoring, mdscoring); `cgtoaa` is rejected by the resolver; and
rigidbody/flexref/emref/mdref additionally fail whenever `ambig_fname` points at an
archive. Neither exclusion is a property of the job — both are fixable in the scanner.

---

## Non-blocking, but they belong in stage 1

### The completeness guard does not cover what it exists for

`_assert_canonical_script` is the structural discharge the taxonomy asks for (0b.10 →
Axes 1 and 2 become corollaries). It checks the work dir, the job-specific dependency
basenames, the declared output basenames, and a step-folder regex. It passed B2 and B3
without complaint, because:

- module/toppar dependencies are deliberately excluded from the leak-name list, and
  nothing checks `str(module_dir)` / `str(toppar_dir)` — an install-path leak is invisible;
- it only looks for the *declared* output names, so a script writing a different real
  filename (B1's mispairing) sails through;
- canonical names are blanked to `\x00` before scanning, so B3's
  output-rewritten-to-an-input-pin is invisible by construction.

Three cheap additions close all three: reject `str(module_dir)`/`str(toppar_dir)`; assert
`$output_pdb_filename` equals a canonical output name; assert each canonical output name
appears exactly once and no output name equals an input pin.

### `tolerance` is a pure orchestration parameter and is in every key

```
0_topoaa/molA.inp:        eval ($tolerance=5)
1_rigidbody/…inp:         eval ($tolerance=5)
2_emref/…inp:             eval ($tolerance=5)
```

`tolerance` is `group: module`, *"Percentage of allowed failures for a module to
successfully complete"*, consumed only by `BaseHaddockModule.export_io_models`, and
referenced by no CNS script. Demonstrated: setting `tolerance = 10` on rigidbody changes
the canonical script by exactly one line, so it changes every key in the step. The
taxonomy names it explicitly as Axis 3.4 MUST-HIT.

`log_level` also reaches the `.inp`, but genuinely reaches CNS (`if ($log_level =
"verbose")`) and changes only the `.out` log. That is a judgment call rather than a
defect — but it is in the key today, so a logging-verbosity change currently invalidates
every cache entry. It deserves a recorded verdict either way.

`cns_params()` is a **deny-list** (`CNS_ORCHESTRATION_PARAMS`), which today happens to
equal the twelve keys of `modules/defaults.yaml` exactly. The taxonomy rules out this
construction for exactly the reason `tolerance` demonstrates: under a deny-list a
parameter that is not a global leaks into the key by default. Explicit inclusion is the
structural fix; the obstacle is the expandable parameters (`mol_*`, `fle_*`), which is
solvable.

### Iteration-1 scope concerns that still stand

- **The rigidbody scheduling rewrite is a science change.** `_sample_models_to_dock` is
  genuinely prefix-stable (job *k* = `models[k % n]`, seed = `iniseed + k`) where the old
  `sampling_factor` scheme was not, and it probably belongs here — but it changes
  user-visible job counts and the combination pairing, with **no CHANGELOG entry, no
  docs, and no test**:

  ```
  sampling=1000 combinations=3:  old jobs=999  new jobs=1000
  sampling=  10 combinations=4:  old jobs=  8  new jobs=  10
  sampling=   5 combinations=4:  old jobs=  4  new jobs=   5
  combination assignment:        combination-major -> round-robin
  ```

  It is still the only behaviour change on the branch and still the only untested thing
  on it.

- **The canonicalization machinery has no production caller.** `CNSJob.canonical_mapping`
  is the only entry point and nothing in a run invokes it, which is precisely why B2, B3
  and B5 can only be found by deliberately probing. `write_cns_dependencies` and the
  `CNS_DEPENDENCIES` manifest have no caller at all and are stage-2 cache machinery that
  does not belong in a bitwise-reproducibility commit.

- **The tests are still toy scripts.** All 404 lines of `test_libcnscanonical.py` build
  3–8 line hand-written CNS inputs. The new cases are welcome and do pin iteration 1's
  gaps 3 and 4 — first-reference pin order, `@MODULE:`-absolute and `TOPPAR/`-slash
  resolution, unresolved-read rejection. But nothing exercises a generated `.inp`, which
  is why B2, B3 and B5 are all invisible to the suite. **One committed real `.inp` per
  shape, canonicalized and compared against a golden canonical form, would have caught
  all three** — and it is what taxonomy 0b.7 asks for anyway.

### Smaller

- Normalization deletes `REMARK FILENAME=`, `REMARK DATE:`, `REMARK HADDOCK stats for`
  and `REMARK initial structure N - …` from every output PDB. No Python code reads them,
  so nothing breaks — but it is a user-visible removal of provenance from scientific
  output files, with no CHANGELOG entry.
- `check_combination_chains` is now called `sampling` times rather than once per
  combination — O(sampling) redundant PDB parses on the default path. `ci` in
  `prepare_cns_input_parallel` is now unused.
- `sampling_factor` is passed as a literal `1` at both rigidbody call sites; the
  parameter is vestigial there.
- `output_files` and `output_pdb_files` are two parameters for one concept, merged in the
  `CNSJob` constructor and re-merged in `build_canonical_mapping`.
- `is_normalized_cns_artifact` is unused outside tests.
- `normalize_cns_pdb` would corrupt a `.gz` file if handed one (it treats gzip bytes as
  LF records); unreachable today because both call sites gate on the `.pdb`/`.psf` suffix
  — but `GridInterface._normalize_output` gates on `path.suffix`, so a grid job returning
  `foo.pdb.gz` is silently skipped instead.
- `CNSJob.work_dir = Path.cwd().resolve()` captures ambient state at construction. Correct
  today (jobs are always built in the step directory) but undocumented and load-bearing
  for both normalization and canonicalization.
- Two unrelated import removals rode along: `KnownCNSError` in `libsubprocess` and
  `HaddockModel` in `mdscoring` (both genuinely unused). `Iterable` is imported from
  `typing` rather than `haddock.core.typing`.

---

## What would close stage 1

1. **Make `libparallel.Scheduler` return results in submission order.** Without this the
   branch's own artifacts are non-deterministic on the default path, and Axis 4 cannot
   even be measured.
2. **Stop install paths reaching the canonical script** in topoaa/topocg, and make
   `_assert_canonical_script` reject them so it cannot regress.
3. **Fix the basename-collision rewrite** so an output cannot be renamed to an input pin,
   stop rewriting variable names, and assert the output binding in the guard.
4. **Seed the CG dummy-bead generator** in `libaa2cg.py`, or declare topocg/cgtoaa out of
   scope for stage 1 — but declare it, with a test.
5. **Fix the stale `$ambig_fname` assignment** (or exclude non-read config values from
   the leak scan), and either **resolve the `$prefix_$var` symbol splice** so `cgtoaa`
   canonicalizes — the read-set is fully determined by the `.inp`, so this is a scanner
   gap, not a property of the job — or declare the shape out of scope with a test that
   asserts it fails loudly.
6. **Drop `tolerance`, decide on `log_level`, and return `cns_params()` to explicit
   inclusion.**
7. **Add one golden canonical form per shape**, generated from a real `.inp`.

Items 1–5 are the difference between "the contract's identity model is possible" and
"not yet". Items 6–7 are what stops the next iteration from re-discovering them.

The encouraging part is genuine and was measured, not inferred: on the perturbations
stage 1 names, an all-atom workflow now produces bit-identical artifacts and identical
keys across every one of them, and it correctly misses when the binding changes. The
identity model works. What is left is four places where it is not yet applied
consistently, and one place — the CG conversion — where a pre-existing source of
randomness makes it inapplicable until fixed.

---

## Appendix: agreed disposition after review

This appendix records the decisions made after discussing the findings above. It is
the implementation brief for the next revision of `bitwise-reproducibility`. Unless a
finding is explicitly qualified here, the fixes recommended by this review still
apply.

### Scheduler result ordering

Fix `libparallel.Scheduler` so that `results` are returned in submission order.
This is a pre-existing scheduler defect: completion-order collection is also present
on `main`. The branch did not introduce the reordering, but its positional output
normalization made the defect consequential. Documentation and commit messages must
describe it as a pre-existing behavior whose impact was exposed by this branch, not as
a regression introduced by the branch.

### Logical bytes and compressed inputs

Checksums and identities are calculated from logical, uncompressed bytes. The existing
compression-transparent checksum rule is correct and must be retained: compressed and
uncompressed storage of identical content has the same identity. The `.tgz` restraint
failure is unrelated to hashing archive container bytes; it is the stale generated CNS
assignment described in B5 and must be fixed as such.

### CNS parameters and identity

- `tolerance` is Python-side module orchestration. Remove it from generated CNS input
  and therefore from CNS-job identity, while retaining its current use when HADDOCK
  decides whether the module produced enough successful outputs.
- `log_level` must continue to control CNS logging during execution, but it must not
  affect the identity of cached PDB/PSF results. The checksum-side canonical form must
  erase or normalize this logging-only distinction.
- Replace the parameter deny-list with explicit inclusion of CNS-affecting parameters,
  including the expandable molecule and flexible-segment parameter families. This must
  structurally prevent future orchestration parameters from entering CNS input by
  default.

### Rigid-body scheduling and seeding

Keep the prefix-stable rigid-body schedule introduced on this branch. Document it as a
change to the scheduling and seeding scheme, not as a “science change.” Specifically:

- model combinations are assigned combination-major in the old scheme and round-robin
  in the new scheme;
- the new scheme produces exactly `sampling` jobs instead of truncating the count to a
  multiple of the number of combinations; and
- seeds remain `iniseed + job_index`, but their association with model combinations
  changes because the combination schedule changes.

This needs appropriate user-facing documentation and a changelog entry. It does not
need a dedicated behavioral test. Seeds naturally affect individual outcomes; that is
not by itself a scientific-method change, and a macroscopic change in result quality
would indicate a separate scientific problem.

Remove the now-vestigial rigid-body `sampling_factor` plumbing, and avoid repeating
`check_combination_chains` for every sampled job when each distinct combination can be
checked once.

### Canonical representation: identity-side only

Do not make ordinary CNS execution consume the canonical representation. Normal jobs
must continue to use the existing HADDOCK step layout and generated filenames.
Canonicalization remains a checksum-side identity representation, and Stage 2 cache-key
construction will be its production caller before cache lookup.

Executing every job from a canonical workspace was found technically feasible, but is
rejected as production architecture. On shared HPC filesystems, per-job workspaces and
hardlinks would add substantial directory and metadata pressure. Node-local scratch
would instead require a new staging, content-pooling, lifecycle, capacity, and
cross-filesystem publication subsystem across local, MPI, batch, and grid backends.
That is a disproportionate, potentially massive HADDOCK change. The complete analysis
is recorded in `canonical-representation-consumption.md`.

An executable isolated canonical workspace may still be used as a focused test or audit
instrument. It is not the production execution path. Stage-2-only manifest machinery
with no current caller, such as `write_cns_dependencies`/`CNS_DEPENDENCIES`, should not
be retained in Stage 1 merely to suggest otherwise.

### Canonicalization correctness

Apply the remaining canonicalization fixes from B2, B3, and B5:

- eliminate relocated-install false misses in `topoaa` and `topocg`, and make the
  completeness guard reject leaked module and toppar roots;
- make rewriting context-aware so an input basename cannot consume an output binding;
- never rewrite CNS variable names on assignment left-hand sides;
- validate canonical output assignments and ensure output names cannot equal input pin
  names;
- support `cgtoaa`'s CNS symbol-splice references, such as
  `@@$input_aa_psf_filename_$nchain`, by expanding the prefix to the matching indexed
  symbols already assigned in the generated input and resolving each of those ordinary
  path values. This is a static expansion of the script's declared inputs, not a
  filesystem probe or an ambient runtime dependency;
- conservatively include all matching declared indexed inputs even when a runtime guard
  means CNS may not read every one. This accepted over-declaration can cause a false
  miss, but cannot cause the false hit that an incomplete read-set would permit;
- canonicalize the absolute run-directory spellings currently used by
  `$input_cgtbl_filename_N` through the same resolved-dependency mapping; and
- remove or otherwise neutralize the stale archive-valued `$ambig_fname` assignment so
  jobs using extracted per-job `.tbl` restraints canonicalize correctly.

With these resolver and stale-assignment fixes, `cgtoaa` and archive-restraint jobs are
in Stage 1 scope; neither is intrinsically uncanonicalizable. Add generated-input
coverage for both forms, including a `cgtoaa` case with shape guards.

The amended B5 also identifies a separate, pre-existing `cgtoaa` correctness bug:
`_add_cg_backmapping_arguments` indexes all-molecule AA topology files against a
non-shape-only backmapping-restraint list. A shape molecule placed before non-shape
molecules can therefore misassociate or drop inputs. Record and address that defect
separately; do not hide it inside the canonical resolver change or describe it as a
regression from this branch.

The current erasure of locator-only `$count` values is accepted as correct and is not a
problem requiring further work.

### Deterministic coarse graining

Fix `topocg` dummy-bead placement so it does not use the process-global, unseeded
`random` generator. Use an explicitly seeded generator derived from the configured
`iniseed`, passed through the coarse-graining call chain. Identical inputs and parameters
must then yield identical coarse-grained coordinates without depending on ambient
Python random state.

### Generated-input coverage and completeness checks

Replace the test suite's exclusive reliance on toy scripts with generated, realistic
CNS inputs and golden canonical forms for every supported CNS job shape. Coverage must
exercise topology install paths, topocg basename collisions, cgtoaa indexed references,
and restraint archives as well as the already working all-atom shapes. Keep adjacent
MUST-MISS checks for scientifically relevant bindings.

Strengthen the completeness guard as described above. In addition to rejecting run,
step, module, and toppar paths, it must verify the declared canonical output bindings
directly rather than merely searching for leaked original output basenames.

### Incomplete-output publication: separate focused change

Fix truncated CNS output exposure independently of canonical input handling, in its own
focused commit. CNS should write each declared output to a hidden partial name in the
existing step directory, preserving the logical `.pdb`/`.psf` suffix. After CNS exits,
require a successful process status, absence of known CNS errors, and a complete,
non-empty declared output set. Normalize the partial artifacts, then publish them to
their public names with same-filesystem `os.replace()`.

Stale partials must be cleared as a set on retry. PDB and PSF cannot be made one atomic
filesystem transaction in the flat layout, but both must be validated before either is
published, and a cache result must not be recorded until the complete set is public.
Grid retrieval should analogously copy into a hidden temporary file in the destination
directory, verify and normalize it there, and atomically replace the public path. This
design adds only temporary output names and one rename per artifact; it does not create
per-job workspaces or add input-staging pressure to shared filesystems.

### Remaining smaller findings

- Document the already implemented removal of volatile CNS PDB provenance lines in the
  changelog; do not reverse the normalization behavior.
- The separate `output_files`/`output_pdb_files` constructor interfaces are accepted for
  now and do not require consolidation.
- Remove the test-only `is_normalized_cns_artifact` helper if it has no production use.
- Make normalization safe for compressed suffixes and ensure grid handling recognizes
  compound names such as `.pdb.gz` rather than silently skipping them.
- Document and test the load-bearing rule that a `CNSJob` resolves relative outputs
  against the directory in which the job was constructed; avoid an execution-layout
  rewrite for this point.
- Keep the genuinely unused import removals. Use the project's typing imports
  consistently for any remaining `Iterable` annotation.


---

## Reviewer response to the appendix

The appendix is a faithful disposition: every blocker and nearly every smaller finding is
addressed, and three of its judgments are better than my recommendations — deferring
canonical consumption (with the analysis in `canonical-representation-consumption.md`),
recording the `cgtoaa` `zip` misindexing as a separate pre-existing defect, and the
incomplete-output publication design, which I had not raised. That last one composes
cleanly with what is already on the branch and it is worth saying why: writing to a hidden
partial name is safe *because* `$output_pdb_filename` is erased from the canonical form and
the volatile `REMARK FILENAME=` / `HADDOCK stats for` lines that would carry the temporary
name are already stripped. The temporary name cannot reach identity.

Six amendments, in order of how much they matter.

### 1. `$seed` in topology jobs is `tolerance`'s twin — new finding, not in the review above

`prepare_single_input` draws `$seed` from the shared module-level `RND` stream
(`libcns.py:310`). After this branch it is the **only** remaining caller that does so —
rigidbody derives `iniseed + idx`, and every refinement and scoring module passes
`model.seed`. Its two users are `topoaa` and `topocg`.

The consequence is that a topology job's seed is a function of the molecule's **position in
the run's draw order**, not of the molecule. Running the same two molecules with one extra
molecule prepended:

```
molecules = [molA, molB]          molA seed=62729   molB seed=68893
molecules = [molC, molA, molB]    molA seed=68893   molB seed=63673

molA canonical script:  DIFFERS, in exactly one line   -eval ($seed=62729) / +eval ($seed=68893)
molA artifacts:         .pdb identical, .psf identical
```

And the seed demonstrably does not reach the artifact at all — one real `molA.inp` rerun
across the range:

```
seed=68893  pdb identical=True  psf identical=True
seed=    1  pdb identical=True  psf identical=True
seed=99999  pdb identical=True  psf identical=True
```

The reason is now established rather than merely observed: **the topology recipes read
`$iniseed`, not `$seed`** — `topoaa/cns/generate-topology.cns:347`,
`topocg/cns/generate-topology.cns:196` and `topoaa/cns/build-missing.cns` all do
`set seed=$iniseed end`, and `$seed` appears nowhere in either CNS tree. The
`eval ($seed=…)` line that `prepare_single_input` emits is dead text in a topology job.

So for topology this is the same defect as `tolerance`: a value in the `.inp`, in the key,
not in the artifact. **Severity there is low** — the artifact is unchanged, so downstream
pins are unchanged and a topoaa miss recomputes a two-second job while everything after it
still hits. But it is free to fix, and it is the difference between a **per-molecule**
topology cache and a **per-molecule-list** one: the same receptor topologized in two
workflows with different molecule counts or orders can never match, which forecloses Axis
11.4/11.6 for the most obviously shareable job class.

**`prepare_single_input` is not the only draw site, and fixing it alone makes things
worse.** `libcns.py` draws from `RND` in two places. The second is the
`if seed is None: seed = RND.randint(100, 99999)` fallback in `prepare_cns_input`
(`libcns.py:496-497`), reached whenever a model carries no inherited seed. `PDBFile.seed`
defaults to `None` (`libontology.py:106`) and topoaa never assigns it, so **every model
going straight from topoaa into a refinement or scoring module takes the fallback**.
Verified on a real `topoaa → emref` run: the job's seed was `63673`, which is draw #3 of
the `RND(494)` stream — i.e. the two topology draws, then this one.

There the seed is not dead. For minimisation-only modules it still makes no difference
(emref at seeds 1 / 63673 / 88327 / 99999 gives byte-identical normalized output), but for
MD-based modules it changes the science:

```
mdscoring, same .inp, seed varied:
  seed=63673   HADDOCK score -57.8214
  seed=88327   HADDOCK score -47.6475     normalized PDB differs
  seed=99999   HADDOCK score -47.1345     normalized PDB differs
```

Both shipped `refine-*` examples take that path — `refine-complex-test.cfg` is
topoaa ×3 → **mdref**, `refine-molecules.cfg` is topoaa → **mdscoring** — so an MD
refinement's seed there is a function of how many CNS inputs happened to be prepared
earlier in the same process, not of the job.

**Fix both sites in one change.** The seed usage map across the CNS trees is unambiguous
and makes the shape of the fix obvious:

| module | reads | needs a seed? |
|---|---|---|
| topoaa, topocg | `$iniseed` only | no — `$seed` is dead text |
| cgtoaa, emscoring | neither | no — needs no seed at all |
| rigidbody, flexref, emref, mdref, mdscoring | `$seed` (`set seed $seed end`, 9 sites) | yes, and all five declare `iniseed` |

So: delete the seed from `prepare_single_input`; make `prepare_cns_input` **omit** the
`$seed` line when no seed is supplied rather than drawing one; have the five modules that
read `$seed` fall back to `iniseed + idx` (as rigidbody already does) when the model
carries none; drop `RND` from `libcns`. Note that `emscoring` has no `iniseed` parameter —
that is fine, because it never reads `$seed`; but its call site (and `cgtoaa`'s) should
stop passing `seed=model.seed`, or a dead seed inherited from upstream lands in their keys
and reproduces the same defect one module downstream.

Doing only the first half is worse than doing nothing: removing two draws per molecule
*shifts* the second site's stream, silently changing existing `topoaa → refinement` results
without fixing the order dependence. Because the combined fix does change results for those
workflows, it needs a changelog entry on the same footing as the rigid-body schedule
change.

### 2. `log_level` erasure is safe — and provable by inspection, not by measurement

I first framed this as needing per-shape empirical verification. That was wrong, on two
counts. The `.out` file is not a checksummed job output — only the `.pdb` and `.psf` are,
and `.out` is not normally even retained — so its size is a control showing the parameter
took effect, not evidence about the cached artifact. And the property is settled
statically: **all 34 `$log_level` occurrences in the entire CNS tree are the same guard
idiom**, with nothing else inside any branch.

```
$log_level lines in src/haddock/modules/*/*/cns/*.cns : 34
lines not matching  if|elseif ( $log_level = "verbose"|"normal" ) then : 0
bodies of those branches, deduplicated:
     17  set message=normal  echo=off end
     13  set message=all     echo=on  end
      3  set message=verbose echo=on  end
      1  set message=normal  echo=on  end
```

`set message` / `echo` control interpreter diagnostics only. There is no branch in which
`$log_level` reaches a coordinate, an energy term, or the RNG. So the appendix's decision
is correct for every shape, and the empirical check I asked for is unnecessary — the
rigidbody measurement (quiet/normal/verbose → byte-identical normalized PDB) is a
confirmation of something already visible in the templates.

What is worth keeping is only the governance point, and it is cheap:

- Erase (or better, **normalize to a fixed literal**, as the PSF title already is) the one
  named parameter, so the canonical form stays reviewable and the erasure is visible in a
  golden canonical form — rather than deleting the line or creating a general
  "logging-only" erasure category that a future parameter can be dropped into on a hunch.
- Add the same tripwire proposed below for `$count`: a test asserting every `$log_level`
  occurrence in the templates matches that guard idiom. That is what keeps the static
  justification true rather than merely true today.

### 3. B1 needs a production-side guard, not only the scheduler fix

Fixing `Scheduler` to return submission order is correct and sufficient *today*. But the
appendix also decides that canonicalization has no production caller in Stage 1 — so
"validate canonical output assignments and ensure output names cannot equal input pin names"
runs only in tests. Nothing in a real run would catch a recurrence of the mispairing, and
the failure mode is silent: artifacts that simply never get normalized.

One assertion in `CNSJob` closes the whole class — the declared output must equal the
`$output_pdb_filename` in its own script. It is O(1), needs no canonicalization, needs no
scheduler assumption, and turns any future positional-pairing bug into a loud failure at
job construction. It is also the natural place to collapse `output_files` /
`output_pdb_files`, which the appendix otherwise leaves as-is.

Verified that the invariant is unconditional, so the assertion needs no per-shape special
cases: across 23 jobs covering all nine shapes, the declared output PDB equals
`$output_pdb_filename` in every case; where a PSF is declared (topoaa, topocg) it equals
`$output_psf_filename`; and where none is declared the script has no `$output_psf_filename`
line. Put the check in `__init__` rather than in `normalize_outputs`, so it fires before
CNS runs and before the scheduler is involved.

Worth noting for sequencing: this assertion is independently valuable *before* the
scheduler fix lands. Under the B1 skew it converts silent under-normalization into an
immediate, located failure — had it existed, B1 would have announced itself rather than
having to be hunted.

### 4. Golden canonical forms cannot see under-declaration

(deferred to stage 4, see last appendix below)

### 5. The rigid-body schedule does need a test — just not a scientific one

I agree with the appendix that seeds affecting individual outcomes is not a scientific-method
change and needs no behavioural validation. But prefix stability is not a scientific
property, it is a property of a pure function:

```python
assert len(_sample_models_to_dock(models, n)) == n
assert _sample_models_to_dock(models, n)[:k] == _sample_models_to_dock(models, k)
```

Five lines, no CNS, no fixtures. Prefix stability is the entire reason the rewrite is on this
branch rather than a later one; leaving it unpinned means a future refactor can silently
restore the old renumbering behaviour, and the symptom would be a global cache miss rather
than a test failure.

### 6. Two smaller ones

- **The HPC/batch normalization path stays unmeasured.** The appendix covers grid retrieval
  under incomplete-output publication but asks for nothing on the SLURM/torque path, which
  my review could only read rather than exercise. A stubbed-`sbatch` integration test, or an
  explicit note that it is untested, would close the gap honestly.
- **`$count` erasure deserves a tripwire, not just a verdict.** It is correct today because
  `$count` appears in the templates only as one `display` in `rigidbody.cns` and as
  `$ambig_fname + "_" + encode($count)`. A test that scans the CNS templates and fails when
  a `$count` use appears outside those two forms costs nothing and converts a standing
  assumption into a guard — the same move the golden forms make for the naming rule.

---

## Third appendix: lifecycle of the virtual canonical representation

The canonicalization machinery is **virtual**. It constructs the representation used
for checksum, CNS-job identity, and job-key calculation. It does not materialize that
representation as an execution workspace, and CNS does not consume it. CNS continues to
execute the ordinary generated input and ordinary HADDOCK filenames.

At the present stage, the machinery has no production caller. It is intentionally dead
production code, exercised only by tests and retained for a future Stage 3. Stage 3 is
the actual caching implementation, where calculation of the CNS job key will become the
canonicalizer's production use. The representation remains virtual in that stage and is
still not executed by CNS.

Consequently, present tests—and Stage 3 tests—can check structural properties such as
stable canonical text, stable pin assignment, location independence, checksum behavior,
and rejection of recognized leaks. They cannot properly establish that the declared
dependency set is complete or that the virtual representation is an executable and
faithful rendering of the CNS job. Golden canonical forms do not close that gap.

Proper executable validation is deferred to a future Stage 4. That stage will provide a
dump option which materializes the canonicalized files into an isolated workspace and
runs the dumped CNS job with `seamless-run` outside HADDOCK. Consumption of the
canonical representation there will provide the intended strong test of the virtual
machinery.

Implementers of the current fixes must annotate and document this lifecycle accurately:
do not state or imply that CNS presently executes the canonical form, that the current
machinery has a production caller, or that dependency completeness has already been
proved by execution. The isolated executable instrument proposed in point 4 of the
reviewer response is not part of the current stage; it is deferred together with the
Stage 4 dump facility.
