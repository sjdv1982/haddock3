# Review 3: does `bitwise-reproducibility` deliver stage 1?

Independent review of the third iteration of branch `bitwise-reproducibility`
(`ea89942a7..b4f4549a8`, seven commits), against the stage-1 goal:

> Separate everything needed so CNS jobs have stable canonical inputs/outputs under
> irrelevant changes: run/step/install path, input filename, output filename, structure
> index where it is only a locator, etc. This is not "preserve implementation behavior";
> it is making the contract's identity model possible.

**What was reviewed.** The tree at `b4f4549a8`. Only the diff against the merge base
`ea89942a7`, not the codebase as a whole.

**Method.** Everything below was measured with the shipped CNS 1.3U binary and real
`haddock3` runs, except where explicitly marked *reasoned*. Probes used:

- paired workflows (topoaa → rigidbody → flexref → emref → mdref → emscoring →
  mdscoring) differing only in run-dir name, absolute path depth, both input molecule
  filenames (chosen so alphabetical order reverses), the restraint filename, and `ncores`;
- the same workflow against a HADDOCK3 install relocated to a different root;
- a coarse-grained workflow (topoaa → topocg → rigidbody → cgtoaa → emref), run twice;
- the `.tgz` restraint-archive shape (`docking-multiple-ambig`);
- an 11-step workflow crossing the zero-fill boundary (`0_topoaa` → `00_topoaa`);
- a `CNSJob.run` wrapper recording each job's `canonical_mapping()`, canonical script,
  pin table, checksum tree and a derived job key;
- **a canonical-workspace dump-and-execute harness**: every job's canonical script and
  its declared dependencies materialised into an otherwise empty directory, executed
  with `MODULE`/`TOPPAR` pointing inside it, and the result compared byte-for-byte with
  the in-place artifact — Axis 8's strong form;
- a rigidbody run with scheduling skew injected into `prepare_cns_input`;
- MUST-MISS adjacency probes (molecule swap, `iniseed`, `w_vdw`) and MUST-HIT probes
  (`tolerance`, `log_level`, `ncores`);
- ambient-state perturbation (`TZ=Asia/Tokyo LC_ALL=C LANG=C PYTHONHASHSEED=12345`);
- an `HPCWorker` simulation of a faulty CNS job in batch mode.

---

## Verdict

**No — and this time for a reason that has nothing to do with the identity model.**

Every blocking defect from review 2 is genuinely fixed, and I verified each one rather
than taking it on trust. On the perturbations stage 1 names, the branch's identity model
is now not merely plausible but **demonstrably sound**: I independently reproduced the
canonical form's executability and read-set completeness for **all nine job shapes**
(34 of 34 artifacts bit-identical when executed from a workspace holding only the
declared dependencies). That is the strongest evidence this design has yet produced, and
it is a real advance over iteration 2.

But the branch as pushed **cannot run a docking workflow at all.**

`BaseCNSModule.cns_params()` replaced the parameter deny-list with an *include* rule
derived by scanning the module's `.cns` files for literal `$name` tokens. CNS recipes
address whole parameter families by **splicing loop variables into symbol names**
(`$int_$nmol1_$nmol2`, `$nrair_$nchain1`, `$seg_sta_$nchain1_$nseg`, …), which that scan
cannot see. The rule therefore silently drops 210 interaction-matrix parameters, plus the
random-AIR, semi-flexible-segment, symmetry and NCS families, from every sampling,
refinement and back-mapping module.

```
$ haddock3 <plain two-protein docking config>      # branch tip, unmodified
%XRMULT-ERR: Illegal data types:
    eval($scalfac = $kinter * $scale.int_$nchain1_$nchain2)
RuntimeError: 100.00% of output was not generated for this module
```

```
$ pytest integration_tests/test_rigidbody.py::test_rigidbody_local
FAILED — RuntimeError: 100.00% of output was not generated for this module
```

`rigidbody`, `flexref`, `emref` and `mdref` fail 100% of their jobs; `cgtoaa` drops the
same families. `topoaa`, `topocg`, `emscoring` and `mdscoring` still work. **85 of the
104 shipped example configs** use one of the four broken modules.

To review the rest of the branch I applied a local patch replacing the recipe scan with
the old deny-list plus `tolerance`. **Every positive result below is from that patched
tree**, and is marked so where it matters. The patch touches nothing else on the branch.

---

## A. Blocking

### A1. `cns_params()` under-includes and breaks every docking workflow

The include rule (`base_cns_module.py:83-104`):

```python
recipe_variables = {
    variable
    for recipe in self.cns_folder_path.rglob("*.cns")
    for variable in _CNS_VARIABLE_PATTERN.findall(recipe.read_text(...))   # r"\$([A-Za-z0-9_]+)"
}
return {k: v for k, v in source.items()
        if k.rstrip() in recipe_variables or k.startswith(("mol_", "fle_"))}
```

`$int_$nmol1_$nmol2` contributes the tokens `int_`, `nmol1`, `nmol2` — never `int_1_2`.
So the whole family is dropped. Measured, per module (`params` minus `cns_params()`,
restricted to names matching a splice prefix that actually occurs in that module's own
CNS tree):

| module | dropped | splice-reachable families dropped |
|---|---:|---|
| `rigidbody` | 358 | `int_N_M` (210), `nrair_N` (20), `rair_sta_N_M` (20), `rair_end_N_M` (20), `cNsym_*`/`s3sym_*` (69) |
| `flexref` | 367 | `int_N_M` (210), `seg_sta_N_M` (20), `seg_end_N_M` (20), symmetry (69); plus `nseg1..20`, `ncs_*` |
| `emref` | 368 | same as flexref |
| `mdref` | 366 | same as flexref |
| `cgtoaa` | 291 | `int_N_M` (210), `seg_sta_N_M` (20), `seg_end_N_M` (20), `nseg1..20` |
| `topoaa` | 17 | none |
| `topocg` | 18 | none |
| `emscoring` / `mdscoring` | 19 | none |

Diff of the generated `rigidbody_1.inp`, merge base vs branch: **412 → 162 `eval` lines.**
Beyond the intended removals (twelve orchestration globals, `tolerance`, the stale
`$ambig_fname`), it also drops `iniseed`, `keepwater`, `previous_ambig`, `sampling`,
`w_dist`, `ligand_top_fname` — of which the first five are genuinely unread by
rigidbody's CNS, so those are correct, and only the splice families are fatal.

**The diagnosis is narrow, which is the good news.** The literal-name half of the rule is
sound: there are no `.cns` files under `toppar/`, so nothing a recipe reads lives outside
the scanned tree, and the non-splice names the rule drops are dropped exactly where CNS
does not read them (`dihedflag` is dropped from `emref`/`cgtoaa` and kept for
`flexref`/`mdref`/`mdscoring`; `keepwater` likewise; `ligand_top_fname` is kept for the two
topology modules that read it). Verified independently: on the **unmodified branch**,
`topoaa` with per-molecule `[topoaa.molN]` histidine parameters produces artifacts
byte-identical to the merge base after normalization, and `topocg`, `emscoring` and
`mdscoring` all run correctly. Only the spliced families are lost.

The direction of the failure is the point. The taxonomy asked for **explicit inclusion**
precisely because a deny-list can let an orchestration parameter into the key — a *cache
miss*. A text-scan include rule fails the other way: it silently removes a parameter CNS
needs — a *wrong or absent computation*. That is the trade the taxonomy exists to avoid,
made in the wrong direction.

**What would fix it.** An actual explicit inclusion list is the thing that was asked for
and the thing that is robust: enumerate the CNS-affecting parameter families per module
(or mark them in `defaults.yaml` with a `cns: true` flag, which is where the knowledge
belongs and which `validations.py` can then enforce). If a derived rule is kept, it must
at minimum expand splice prefixes — collect `$prefix_$var` occurrences and admit every
parameter matching `^prefix\d+(_\d+)?$` — which is exactly the resolver capability
`libcnscanonical._resolve_reference` already implements for `@@$input_aa_psf_filename_$nchain`.
Either way it needs a test that asserts, per module, that every `$symbol` the recipe tree
can construct is bound in the generated `.inp` — the same class of tripwire the branch
already added for `$count` and `$log_level`.

### A2. Batch mode loses `tolerance`: one faulty CNS job now aborts the run

`HPCScheduler.run` gained (`libhpc.py:243-245`):

```python
for worker in worker_list:
    if worker.job_status == "finished":
        worker.normalize_outputs()
```

`HPCWorker.normalize_outputs` calls `task.publish_outputs(check_output_log=True)`, which
raises `CNSRunningError` when a task's declared outputs are missing or when its `.out`
contains a known CNS error. `HPCScheduler.run` catches only `KeyboardInterrupt`, so the
exception escapes the module and aborts the whole workflow. Simulated directly:

```
HPCWorker([job_that_produced_nothing], 1).normalize_outputs()
-> CNSRunningError: CNS did not produce complete outputs: /tmp/.../.out_1.partial.pdb
```

On `main` a faulty CNS job in batch mode simply left no PDB and was absorbed by
`export_io_models(faulty_tolerance=...)`. The local path is unaffected, because
`libparallel.Worker.run` catches per-task exceptions. So the failure is confined to
`mode = "batch"` — the path with no end-to-end test.

Two related batch-mode changes in the same commit:

- `worker.normalize_outputs()` runs only for workers whose status is `"finished"`. With
  `concat > 1`, a worker that ends in any other terminal state never publishes **any** of
  its partial outputs, so models that did succeed are discarded along with the one that
  did not.
- the job file gained `|| exit $?` after each CNS invocation. CNS 1.3 exits 0 on a
  parse/ABORT error (that is why `contains_cns_stdout_error` exists), so this only fires
  on hard failures — but when it does, it aborts the remaining jobs in a `concat` batch,
  which previously all ran. *Reasoned from the code; no SLURM here.*

Fix: catch and log per task inside `normalize_outputs`, publish per task independently of
worker status, and let `tolerance` make the decision as it does everywhere else.

---

## B. Confirmed fixed (verified, not assumed)

| Review-2 blocker | Status |
|---|---|
| **B1** `Scheduler.results` in completion order → mispaired outputs | **Fixed.** `Worker` now returns `(index, result)` and `Scheduler` sorts. Real run, `sampling=8`, `ncores=4`, 3 s delay injected into the first chunk's `prepare_cns_input`: all eight `.inp` files declare their own output (`rigidbody_k.inp` → `rigidbody_k.pdb`), seeds run 918…925 in order, all eight PDBs produced and normalized. |
| **B2** 62 absolute install paths in every topology canonical script | **Fixed.** Zero occurrences of the install root in any of the 14 canonical scripts of the all-atom workflow. A third run against a **relocated install** produced 14/14 identical job keys and 16/16 identical artifacts. |
| **B3** topocg output binding consumed by a same-basename input; CNS variable names rewritten | **Fixed.** The shape job's canonical script now reads `eval ($file="canonical-input-1.pdb")` … `eval ($output_pdb_filename="canonical-output.pdb")` — variable name preserved, binding correct. `_assert_output_bindings` asserts it. |
| **B4** topocg nondeterministic (unseeded global `random`) | **Fixed.** `martinize(..., seed=defaults["iniseed"])` threads a `random.Random`. Two CG runs of a byte-identical config: **18/18 artifacts identical**, where iteration 2 differed at every step from topocg onward. |
| **B5a** `cgtoaa` rejected: `@@$prefix_$var` symbol splice | **Fixed.** `_INDEXED_VARIABLE_REFERENCE_PATTERN` expands the prefix to the already-assigned indexed symbols. Both `cgtoaa` jobs canonicalize; the absolute `$input_cgtbl_filename_N` spellings are rewritten to `canonical-cg-input-N.tbl`. |
| **B5b** `.tgz` restraint archive rejected: stale `$ambig_fname` | **Fixed.** `prepare_cns_input` pops `ambig_fname` from the defaults before `load_workflow_params`. All three `docking-multiple-ambig` rigidbody jobs canonicalize and pin `ambig_1/10/11.tbl` by content — three distinct keys. |
| `tolerance` in every key (Axis 3.4) | **Fixed.** `tolerance = 33` on rigidbody → all four job keys unchanged. |
| `log_level` in every key | **Fixed** as the appendix decided: still reaches CNS, normalized to a fixed literal in the canonical form. `log_level = "verbose"` → all four keys unchanged. Tripwire test asserts all `$log_level` uses are the guard idiom. |
| `$seed` drawn from a shared `RND` stream (reviewer amendment 1) | **Fixed at both sites.** `RND` is gone from `libcns`; `prepare_single_input` emits no `$seed`; `prepare_cns_input` omits the line when none is supplied; the five modules that read `$seed` fall back to `iniseed + idx`. Verified: swapping the molecule list leaves **both topoaa keys unchanged**, which iteration 2 could not do. The `$seed`/`$iniseed` usage map is exactly as review 2 described (topoaa/topocg read `$iniseed` only; cgtoaa and emscoring read neither and are now given neither). |
| Output-binding assertion in production (reviewer amendment 3) | **Fixed.** `CNSJob._assert_declared_output_bindings` runs in `__init__`, before CNS and before the scheduler. All nine module call sites declare their outputs. |
| Stage-2 machinery in a stage-1 commit | **Fixed.** `write_cns_dependencies` / `CNS_DEPENDENCIES` are gone. The module docstring states the lifecycle correctly: checksum-side only, no production caller, executable validation deferred. |

### Verified end-to-end (patched tree)

Two runs differing in run-dir name, absolute path depth, both molecule filenames
(order-reversing), the restraint filename and `ncores` (2 vs 4):

```
canonical script + pin checksums:  IDENTICAL for all 14 jobs
output artifacts:                  IDENTICAL for all 16 .pdb/.psf files
relocated install (third run):     IDENTICAL 14/14 keys, 16/16 artifacts
coarse-grained pair:               IDENTICAL 18/18 artifacts, 12/12 jobs canonicalize
```

Also verified:

- **Zero-fill boundary (Axis 2.5).** 2-step vs 11-step workflow (`0_topoaa` → `00_topoaa`):
  topoaa and rigidbody keys unchanged.
- **Ambient process state (Axis 8.6).** `TZ`/`LC_ALL`/`LANG`/`PYTHONHASHSEED` perturbed:
  artifacts identical.
- **MUST-MISS adjacency.** Molecule swap, `iniseed`, and `w_vdw` each miss on rigidbody
  and hit on topoaa (except the swap, which correctly leaves topoaa alone). The key is
  not merely over-invariant.
- **Prefix stability (Axis 4).** `_sample_models_to_dock` is a pure function of the job
  index: `f(models, n)[:k] == f(models, k)` for all `k ≤ n`, and `len(f(models,n)) == n`.
  The `ambig_fnames[k % n_diff]` assignment is prefix-stable for the same reason.
  *(A direct sampling 2→3 run shows rigidbody keys changing, but the diff is exactly one
  line, `eval ($sampling=…)`, which my review patch reintroduced and the branch excludes.)*
- **Normalization completeness.** All 41 `.pdb`/`.psf` artifacts across three completed
  workflows pass `is_normalized_cns_*`.
- **Normalization is exactly the delta.** With the parameter regression patched out, all
  16 artifacts of the full seven-module workflow are byte-identical to the merge base's
  after applying `normalize_cns_{pdb,psf}_bytes` to the base's. The only removals are
  `REMARK FILENAME=`, `REMARK DATE:`, `REMARK HADDOCK stats for`, `REMARK initial
  structure N - …`, and the PSF `DATE:` line; the PSF `; FILENAME=` line is rewritten to a
  fixed literal. Nothing else changes.
- `$structures` is now referenced nowhere; `$ini_count` survives only as a
  self-assignment in five other recipes. Both removals are safe.

### The strongest result: the canonical form is executable and its read-set is complete

I dumped every job's canonical script plus **only its declared dependencies** into an
empty directory, pointed `MODULE`/`TOPPAR` inside it, ran CNS, and compared:

```
shapes covered : topoaa, topocg, rigidbody, flexref, emref, mdref,
                 emscoring, mdscoring, cgtoaa            (all nine)
workspaces     : 33   (all-atom ×14, coarse-grained ×12, tgz-restraint ×7)
CNS exit status: 0 everywhere
artifacts      : 34 of 34 bit-identical to the in-place run after normalization
```

This is the taxonomy's Axis 8 strong form ("execute a job in an environment containing
*only* its declared dependencies"), which it calls the single highest-value test in the
document. It also incidentally validates two erasures that could only be argued
statically before: `$log_level → "canonical-log-level"` and `$count → canonical-count`
are both executable-safe, including in the `.tgz` case where `$ambig_fname + "_" +
encode($count)` has to fall back to the base restraint file.

I record this as an independent finding, not as a claim about the branch's own tests: the
branch contains no such harness, and the third appendix is explicit that executable
validation belongs to a later stage. But the property holds today, and it is worth
knowing that it does.

---

## C. New findings, non-blocking

### C1. The executed script is not the `.inp` on disk

`CNSJob.run` no longer streams the `.inp` into CNS. It reads the script, rewrites
`$output_pdb_filename`/`$output_psf_filename` to hidden partial names, and pipes that.
With `debug = true`:

```
rigidbody_1.inp   : eval ($output_pdb_filename="rigidbody_1.pdb")
rigidbody_1.out.gz: CNSsolve>eval ($output_pdb_filename=".rigidbody_1.partial.pdb")
```

The retained input and the retained log disagree about what ran, and re-running the
retained `.inp` by hand produces an un-normalized artifact. Identity is unaffected —
`$output_pdb_filename` is erased from the canonical form and the filename-bearing REMARKs
are stripped — but the two provenance artifacts a user reaches for when debugging now
contradict each other. Worth either writing the executed script as the `.inp`, or saying
so in the docs.

### C2. CNS side files are silently renamed into hidden files

`$failfile` and `$dispfile` are derived inside CNS from `$output_pdb_filename`
(`topoaa/cns/generate-topology.cns:677,753`, `topocg/…:245`, `rigidbody.cns:623`,
`mdscoring.cns:464`). Under partial naming, topoaa's high-bonded-energy warning is now
written to `.molA_haddock.partial.warn` instead of `molA_haddock.warn`. Since
`libio.glob_folder` uses `glob.glob`, which skips dotfiles, these are invisible to
`clean_steps`, `haddock3-clean` and `gen_archive`. No Python code reads them, so this is
purely a diagnosability loss — but it is a silent one. *Reasoned from the code; no run in
this review triggered a `.warn`.*

### C3. Failed jobs leave hidden partial artifacts behind

On the CNS error path `publish_outputs` is never reached, so the truncated partial stays:

```
$ ls -a runR/1_emref/
.emref_1.partial.pdb   emref_1.cnserr.gz   io.json   params.cfg
```

Same dotfile-invisibility as C2: never cleaned, never archived, accumulates across
retries of a run directory.

### C4. `.out` files are now written on every path

On `main`, `CNSJob.run` wrote `self.output_file` only in the `isinstance(input_file, Path)`
branch, i.e. only with `debug = true`. The rewritten `run` always writes it:

```
main   0_topoaa/  ->  *_haddock.pdb  *_haddock.psf  io.json  params.cfg
branch 0_topoaa/  ->  *_haddock.pdb  *_haddock.psf  *.out.gz  io.json  params.cfg
```

Arguably an improvement, but it is an unannounced change in what a run directory
contains and in its size — a CNS `.out` is 15 k lines, and a 1000-model rigidbody step
now carries 1000 of them.

### C5. `martinize()` is still unseeded in `caprieval` and `caprifilter`

`libaa2cg.martinize` grew a `seed` parameter and `topocg` passes `iniseed`. The other two
callers — `caprieval/__init__.py:127` and `caprifilter/__init__.py:138`, which coarse-grain
the *reference* structure — do not, so `random.Random(None)` still jitters the dummy
beads there. Analysis-only, so outside stage-1 CNS reproducibility, but it makes reported
CAPRI metrics for CG runs irreproducible for exactly the reason B4 fixed one module over,
and the fix is the same one line.

---

## D. Appendix items not carried out

| Appendix / reviewer-response item | Status |
|---|---|
| **Golden canonical forms from generated `.inp`, one per shape** | **Not done.** All 566 lines of `test_libcnscanonical.py` still build 3–10 line hand-written scripts. Nothing in the suite touches a generated `.inp`. This is the specific gap that let A1 through: a golden canonical form for a real `rigidbody_1.inp` would have shown 250 `eval` lines disappearing. |
| **Prefix-stability test for `_sample_models_to_dock`** | **Not done.** The property holds (verified above); the five-line pure-function assertion the appendix asked for is absent. |
| **`check_combination_chains` once per distinct combination** | **Not done.** `prepare_cns_input_parallel` now iterates `sampled_models_to_dock`, which has `sampling` entries with repeats, so the call count went from `n_combinations` to `sampling`. |
| **Changelog entry for the removal of volatile PDB provenance lines** | **Not done.** The four CHANGELOG entries cover atomic publication, CG seeding, the rigid-body schedule and the seed fallback. Nothing says that `REMARK FILENAME=`, `REMARK DATE:`, `REMARK HADDOCK stats for` and `REMARK initial structure N` are now deleted from every output PDB. |
| **Scheduler ordering framed as pre-existing** | **Not done in the commits.** Six of the seven commits have an empty message body; only `3a295b202` carries an explanation. The appendix specifically required that the `libparallel` change be documented as a pre-existing defect exposed by this branch, and there is nowhere that says so. |
| **Remove `is_normalized_cns_artifact` if unused in production** | **Not done.** Still test-only. |
| **HPC/batch normalization exercised** | Partially: `test_hpcworker_normalizes_task_outputs` is a monkeypatched unit test. No stubbed-`sbatch` integration test — and see A2 for what that gap is currently hiding. |

Also worth noting, in the branch's favour: the `$count` and `$log_level` tripwire test
(`test_logging_and_count_canonicalizations_are_limited_to_known_cns_uses`) is exactly the
mechanism reviewer-response items 2 and 6 asked for. It uses a repo-relative path, so it
only works when pytest runs from the repo root; it fails loudly rather than vacuously
elsewhere, which is the right failure mode, but a `Path(__file__).parents[1]` would be
better.

---

## E. Smaller

- `cns_params()` re-reads the module's whole CNS tree on each call, and `topoaa` calls it
  once per input molecule. Measured at 0.3 ms per call, so this is a note, not a problem.
- `_canonical_dependency_names` matches named roles by substring, so the `("unambig", …)`
  entry must precede `("ambig", …)` in `named_roles` for `unambig_fname` to bind correctly.
  It does, and the `.tgz` run confirms both roles bind correctly — but the ordering is
  load-bearing and undocumented.
- The published `.psf` now carries `; FILENAME="canonical-output.psf"` regardless of its
  actual name. Nothing in HADDOCK3 reads that field, and a fixed literal is the right
  choice for identity, but a user opening the file sees a title that names a file that
  does not exist.
- `haddock.libs.libmath.RandomNumberGenerator` now has no production caller at all.
- Two long lines in `libsubprocess.py` (342, 363) exceed 88 characters; the project has no
  ruff config, so `E501` is not enforced and this is cosmetic.
- The pre-existing `_add_cg_backmapping_arguments` misindexing (all-molecule
  `aa_psf_list` zipped against non-shape-only `cgtoaa_tbl_list`) is untouched, as the
  appendix decided. Still open, still worth its own issue.
- Grid path: `GridInterface.retrieve_output` raising `RuntimeError` on an incomplete
  transfer is harmless in practice because `process_job` is invoked through
  `ThreadPoolExecutor.map` whose iterator is never consumed, so the exception is
  discarded. That is pre-existing behaviour, not a branch change, but it means the new
  incompleteness check cannot actually report anything. *Reasoned; no DIRAC here.*

---

## What would close stage 1

1. **Fix `cns_params()`.** Explicit per-module inclusion (or a `defaults.yaml` flag), or a
   derived rule that expands `$prefix_$var` splices. Plus a per-module test asserting every
   constructible `$symbol` is bound in the generated `.inp`.
2. **Restore `tolerance` semantics in batch mode**: publish per task, catch per task, do
   not let one faulty CNS job abort the workflow.
3. **Add the golden canonical forms** — one real generated `.inp` per shape, canonicalized
   and diffed against a committed golden form. This is the item that pays for itself
   immediately: it is what would have caught item 1.
4. The three small carry-overs: the prefix-stability assertion,
   `check_combination_chains` once per combination, and the changelog entry for the
   provenance-line removal. Plus commit-message bodies, particularly for the `libparallel`
   change.
5. Decide what to do about C1–C3 (executed script vs `.inp`, hidden `.warn`/`.fail`, and
   orphaned partials) — none is a correctness problem, but all three degrade the run
   directory as a diagnostic record, which is the thing users reach for when a job fails.

The distance is short. Items 2–5 are small and local. Item 1 is the only substantial one,
and it is a bug in a mechanism, not a flaw in the design — the identity model underneath
it is now working, and I could not break it.

---

## Reproducing this review

Probe artifacts, worktrees and run directories are left in place under
`/home/agent/tmp/review3/`:

- `bwr/` — worktree of `b4f4549a8` with the one-function review patch to
  `BaseCNSModule.cns_params` (a deny-list). Used for every result marked "patched tree".
- `base/` — worktree of the merge base `ea89942a7`.
- `relocated-install-elsewhere-deeper/` — copy of `bwr` at a different root.
- `work/runprobe.py` — records `canonical_mapping()` per job.
- `work/dumpprobe.py` — materialises the canonical workspace per job.
- `work/canon*/`, `work/dump*/` — the recorded mappings and executed workspaces.

`git worktree remove` on the three worktrees and `rm -rf /home/agent/tmp/review3` cleans
up; nothing was written into the working tree of `/home/agent/haddock3` except this file.
