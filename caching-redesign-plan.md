# Caching as a native feature of local mode

## Context

HADDOCK3 currently gets content-addressed caching by way of a separate execution
mode. `mode = "seamless"` selects `SeamlessScheduler`, and every CNS job is staged
into a temporary workspace and executed through the `seamless-run` subprocess CLI,
which content-addresses the staged inputs and serves a cached result on a repeat.
That design carries the whole Seamless execution stack — `seamless-init`,
`seamless-upload`, a configured cluster, hashserver/database/jobserver — as the
price of admission for what is really just result reuse.

This change removes the execution dependency and keeps the caching. Seamless
remains a dependency **as a library**: its checksum machinery is imported and
called in-process, with no subprocess, no bash, and no servers. Caching becomes an
ordinary property of local mode — **writing the cache is always on**, and **reading
it is opt-in via a new `--cache` command-line argument** naming a previous run
directory.

Two consequences shape the whole design:

1. Because CNS again runs directly in the step directory rather than inside a
   sandbox containing only its declared dependencies, **a missed dependency no
   longer fails loudly**. It silently yields an under-specified cache key, which is
   the one failure mode that produces confidently wrong results. Several elements
   below exist solely to counteract this.
2. The job key must be invariant to everything that does not change the
   computation. Today it is not: staged paths embed the step ordinal and its
   zero-fill width, so inserting a module — or merely pushing a workflow past ten
   steps — invalidates every entry.

Intended outcome: re-running a workflow against a previous run's results reuses
every CNS job whose inputs are unchanged, with an integrity check on every reuse,
and with no Seamless services running anywhere.

> **Note on structure.** The conversation that produced this plan proceeded in six
> explanatory steps. Those were an explanation order, **not an implementation
> order**. This document is organised by component; a suggested build order is at
> the end.

---

## 1. Remove the seamless execution mode

Delete the mode and everything that branches on it.

| Location | Change |
|---|---|
| [modules/defaults.yaml:36-52](src/haddock/modules/defaults.yaml#L36-L52) | drop `seamless` from `mode.choices`; rewrite the `long` help, which currently documents `seamless-run` wrapping |
| [modules/__init__.py:403](src/haddock/modules/__init__.py#L403) | drop from `EngineMode` Literal |
| [modules/__init__.py:442-447](src/haddock/modules/__init__.py#L442-L447) | delete the `get_engine` seamless branch |
| [modules/__init__.py:464-469](src/haddock/modules/__init__.py#L464-L469) | drop from `available_engines` |
| [clis/cli.py:169-171](src/haddock/clis/cli.py#L169-L171) | delete the `initialize_seamless_run` call |
| [base_cns_module.py:80-82](src/haddock/modules/base_cns_module.py#L80-L82) | `cns_input_as_file()` keys on `debug` only |
| [modules/analysis/__init__.py:42-68](src/haddock/modules/analysis/__init__.py#L42-L68) | drop `seamless` from the downgrade tuple |
| [libsubprocess.py:250-256](src/haddock/libs/libsubprocess.py#L250-L256) | delete the `execution_mode == "seamless"` dispatch and `_run_with_seamless` |
| [libseamless.py:44-66, 111-164, 383-421](src/haddock/libs/libseamless.py) | delete `initialize_seamless_run`, `SeamlessScheduler`, `_bulk_upload_dependency_sidecars` and the `.CHECKSUM` sidecar machinery |
| [rigidbody/__init__.py:273](src/haddock/modules/sampling/rigidbody/__init__.py#L273) | `if self.params["mode"] != "local"` currently forced seamless down the sequential CNS-input path; verify the parallel path is correct now that local is the only cached mode |

`SEAMLESS_CACHE`, `seamless-init`, `seamless-upload` and the staged-execution path
all disappear from the runtime. `scan_cns_dependencies` and the canonical-bucket
logic in `_canonical_stage_path` are **kept and repurposed** (§2, §3).

---

## 2. The job checksum, computed in-process

New home: `src/haddock/libs/libseamless.py`, retained as the **quarantine module**
for everything that touches Seamless API. Nothing outside it may import `seamless`.
This boundary is deliberate: computing "the checksum `seamless-run` would produce,
without invoking it" belongs upstream in Seamless and is expected to move there.
Reaching into semi-private API (`seamless_transformer.cmd.*`) is acceptable *because*
the code is a temporary host; when it migrates, extraction should be deleting an
implementation behind one call site, not unpicking imports scattered through the CNS
layer. Pin a Seamless version accordingly — private API carries no compatibility
promise.

Public surface, roughly:

```python
def job_checksum(job: CNSJob) -> tuple[str, CanonicalMapping]: ...
def result_checksum(job: CNSJob, mapping: CanonicalMapping) -> str: ...
```

### 2a. Canonicalization

**Purpose.** Make the key invariant to anything that does not change the
computation: absolute install and run locations, the step ordinal and its zero-fill
width, and arbitrary per-job basenames. Two jobs that differ only in which ensemble
member they dock must produce a **bit-identical canonical `.inp`**, so that their
identity rests entirely on the *content* of their canonically-named inputs. That is
what makes a key survive renames, renumbering and reordering — and what lets a run
reuse a previous run's results at all.

**This is a checksum-side rewrite only.** CNS continues to execute in the step
directory under its real filenames; nothing about the run-dir layout or downstream
naming changes. The canonical form exists purely to be checksummed.

**The reference set is larger than it first appears.** `prepare_cns_input` writes
input paths *into* the `.inp` as CNS statements
([libcns.py:248-273](src/haddock/libs/libcns.py#L248-L273)), so canonicalizing a file
tree accomplishes nothing on its own — the `.inp` text itself must be rewritten.
Verified job-varying inputs:

- the `.inp` (seed, `count`, output name, segids, embedded paths)
- **N input PDBs** — `coor @@<pdb>` per molecule; N ≥ 2 for rigidbody, 1 for
  refinement/scoring
- **N input PSF topologies** — `structure @@<psf> end` per molecule; ensemble
  members carry different ones
- **the ambiguous restraints `.tbl`** — genuinely per-job:
  [rigidbody:131-135](src/haddock/modules/sampling/rigidbody/__init__.py#L131-L135)
  indexes `ambig_fnames[idx - 1]`;
  [emref:76-92](src/haddock/modules/refinement/emref/__init__.py#L76-L92) carries each
  model's `restr_fname` forward

`unambig_fname`, `hbond_fname` and the ligand parameter/topology files are constant
per step but still enter the tree.

**Procedure.**

1. Take the generated `.inp` **text in memory**. It is no longer materialized to
   disk except under `debug` — the forced write that `cns_input_as_file()` performed
   for staging is gone.
2. Resolve every file reference with the *same* logic `scan_cns_dependencies`
   already implements — `@`/`@@` includes, `$var`/`&var` assignment resolution,
   `MODULE:`/`TOPPAR:` prefixes, optional-reference guards, and the dynamic
   `$ambig_fname + "_" + encode($count)` pattern handled by
   `_extract_dynamic_toppar_prefix`. The rewrite and the dependency scan **must
   share one resolution pass**, so they cannot disagree about which files a job
   reads.
3. Assign a canonical name by role, deterministically ordered by position of
   emission in the `.inp`:

   | Role | Canonical name |
   |---|---|
   | the input script | `canonical.inp` |
   | input structures | `canonical-input-{i}.pdb`, `canonical-input-{i}.psf` |
   | restraints | `canonical-ambig.tbl`, `canonical-unambig.tbl`, `canonical-hbond.tbl` |
   | ligand parameters | `canonical-ligand.top`, `canonical-ligand.param` |
   | CNS executable | `canonical-cns` |
   | module CNS templates | `module/<path relative to the module's cns/ dir>` |
   | force field | `toppar/<path relative to src/haddock/cns/toppar/>` |
   | CNS stdout | `canonical.out` |
   | outputs | `canonical-output.pdb`, `canonical-output.psf` |

   `module/` and `toppar/` deliberately keep their real relative names: those are
   stable identifiers belonging to the HADDOCK3 source, and renaming one *should*
   invalidate. Only per-job and per-run varying names are canonicalized.

4. **An unclassifiable reference is a hard error.** The current `external/` bucket
   falls back to `stage_dir / "external" / <absolute path>`, which embeds the
   machine's directory layout in the key. Remove it. Every reference must land in a
   known role.
5. Rewrite the `.inp` text, substituting canonical names in both `@@path` includes
   and `evaluate ($var = "path")` assignments. **Assert completeness**: the canonical
   text must contain no occurrence of the run directory path, the install path, or
   any step-folder name. This guard is cheap and catches an incomplete rewrite
   immediately, which otherwise degrades silently into permanent cache misses.
6. Build the input tree `{canonical name: SHA-256 of content}` using
   `seamless.checksum.calculate_checksum.calculate_file_checksum` — streaming, so
   large files are not read whole into memory. Note that Seamless's own convention
   **decompresses before checksumming** (`strip_compression_suffix` /
   `decompress_bytes`), which matters because `gear/clean_steps.py` gzips step
   outputs after a run.

**What stays in the key, deliberately.** `seed` (`iniseed + idx`) and `count`
(`= idx`) remain per-job scalars in the canonical `.inp`. `count` is load-bearing,
not a label: [emref/cns/read_data.cns:20-24](src/haddock/modules/refinement/emref/cns/read_data.cns#L20-L24)
uses it to select a per-structure AIR file, falling back to the plain one. Neither
costs anything — `seed` already differs per job, so cross-job deduplication was never
available. The reuse this cache targets is **across runs**, where job *i* keeps both
its seed and its count.

### 2b. Transformation construction

Build the bash transformation exactly as `seamless-run` would, then checksum it.
`prepare_bash_transformation(..., dry_run=True)`
(`~/seamless1/seamless-transformer/seamless_transformer/cmd/bash_transformation.py`)
computes the transformation checksum **without contacting any store** — this is the
mechanism that makes the whole approach possible. It returns
`(tf_checksum, transformation_dict)` via `tf_get_buffer(...).get_checksum()`.

Metavars are confirmed **not** part of the checksum: `tf_get_buffer`
(`transformation_utils.py:43-59`) skips every `META__*` key and the orthogonal
dunder keys. With canonical names the wrapper script can be fully literal, so
metavars are dropped entirely rather than canonicalized.

**The transformation shape depends on output arity**, and the in-process path and
the synthesizer must agree on which shape a job takes, because the bash code — and
therefore the job checksum — differs between them:

- **One output (PDB only).** No `-cp` targets, so
  `__output__ = ("result", "bytes", None)` and the generated bash is
  `( code ) > RESULT` ([bash_transformation.py:47-50](file:///home/agent/seamless1/seamless-transformer/seamless_transformer/cmd/bash_transformation.py#L47-L50)).
  The wrapper ends with `cat canonical-output.pdb`, making the result buffer the PDB
  itself. CNS's own stdout is already redirected to `canonical.out`, so it does not
  contaminate `RESULT`.
- **Two outputs (PDB + PSF).** Both declared as `-cp` targets, so
  `__output__ = ("result", "deepfolder", None)`
  ([bash_transformation.py:111-114](file:///home/agent/seamless1/seamless-transformer/seamless_transformer/cmd/bash_transformation.py#L111-L114)),
  and the deepfolder keys are the server-side `-cp` paths
  ([get_results.py:200-213](file:///home/agent/seamless1/seamless-transformer/seamless_transformer/cmd/get_results.py#L200-L213)).

### 2c. Result checksum

- **One PDB output** → plain SHA-256 of the PDB.
- **PDB + PSF** → deep checksum of
  `{"canonical-output.pdb": <sha256>, "canonical-output.psf": <sha256>}`.

Use `seamless.checksum.calculate_checksum.calculate_dict_checksum` — public API,
documented as "compatible with the checksum of a 'plain' cell", and exactly
`orjson.dumps(d, OPT_INDENT_2 | OPT_SORT_KEYS) + b"\n"` then SHA-256. **Never
hand-roll this with `json.dumps`**: key ordering, two-space indentation and the
trailing newline all feed the hash, and a divergence yields a checksum that looks
well-formed and matches nothing.

Keys are the *canonical* output names, so the deep checksum is itself
rename-invariant; the real paths live in the CACHE record.

Checksums are taken of the file **as it finally rests on disk, after
`normalize_output_pdbs()`** has stripped the volatile `REMARK` lines. This is
load-bearing for §6.

**Enforcement:** exactly one PDB and at most one PSF per cacheable job. Verified
against every CNS module today — all declare a single `output_pdb_files` entry;
[topoaa:357](src/haddock/modules/topology/topoaa/__init__.py#L357) and
[topocg:292](src/haddock/modules/topology/topocg/__init__.py#L292) add one `.psf`.
Covering the `.psf` also repairs a live regression: commit `551fb03d3` narrowed the
`-cp` loop from `output_files` to `output_pdb_files`, so topology `.psf` outputs are
currently produced inside the staged workspace and destroyed with it.

---

## 3. The `seamless-run` synthesizer

The staging machinery is not retired — it is promoted to a **reference
implementation**. `synthesize_seamless_run(job, mapping)` produces a complete,
genuinely executable `seamless-run` invocation for a job: a staged workspace under
canonical names, the wrapper script, the input manifest, and the `-cp` flags (or
their deliberate absence) matching the shape chosen in §2b.

It never runs in the normal path. It exists so that "this is the checksum
`seamless-run` would produce" is **testable rather than asserted**. Without it, the
in-process reimplementation can drift from Seamless silently, and the failure mode of
drift is a cache that returns confidently wrong data.

Requirements:

- **One canonical mapping, two consumers.** The synthesizer and the checksum path
  must consume the *same* `CanonicalMapping` object. If they canonicalize
  independently, agreement between them proves nothing.
- **Both shapes.** Single-output jobs synthesize with no `-cp` and a wrapper ending
  in `cat canonical-output.pdb`; two-output jobs synthesize with both `-cp` targets.
- **Executable as emitted.** A synthesized job runs in an isolated workspace
  containing *only* declared dependencies, so running it over representative jobs in
  CI **also validates the dependency scan** — a missed file makes CNS fail there,
  exactly as it does today. For the tested subset this recovers the enforcement lost
  with the sandbox.
- When this functionality moves upstream into Seamless, the synthesizer is the
  specification it must satisfy.

**Debug output.** Under `debug`, write each synthesized command to
`<run_dir>/cached-commands.sh` — a single executable bash file, appended under the
same lock discipline as `CACHE` (§5), with a shebang header written once and each
command preceded by a comment giving the job checksum and expected result checksum.
For those commands to be runnable, **debug mode must retain the staged workspaces**
rather than `rmtree` them: stage under `<run_dir>/<step>/.cache-stage/<job>/` and
leave them in place.

---

## 4. Per-step dependency manifest

Every step that invokes CNS writes a manifest into **its own step subdirectory**.
Contents: a plain newline-separated list of relative paths — no checksums, no
per-job entries — covering only the **step-invariant** dependencies (CNS templates,
force-field files, executables). Paths are relative to the module directory or the
run directory; nothing absolute, so the manifest does not encode where HADDOCK3 is
installed or where the run sits. Reuse the existing bucket classification in
`_canonical_stage_path` as its basis.

**Rationale — this is a mitigation, not a convenience.** Today the sandbox *is* the
check: `seamless-run` executes each job in a workspace containing only what
`scan_cns_dependencies` declared, so a missed dependency fails immediately, on every
job of every run. Once CNS runs normally in the step directory, everything it needs
is present regardless of whether the manifest knows about it, and under-declaration
becomes silent. Note the asymmetry: over-declaring is merely wasteful (spurious
invalidation), while under-declaring is silently wrong. With automatic detection
gone, the dependency set must at least become **visible** — inspectable per step,
diffable between runs and across HADDOCK3 versions, and assertable in tests.

---

## 5. The CACHE file (write side — always on)

A hook in the local-execution path fires after **every** CNS job completes, success
or failure, and appends one line to `<run_dir>/CACHE`.

Four tab-separated fields, always present (trailing fields empty as needed):

| # | Success — PDB only | Success — PDB + PSF | Failure |
|---|---|---|---|
| 1 | job checksum (hex) | job checksum (hex) | job checksum (hex) |
| 2 | SHA-256 of the PDB | deep checksum (§2c) | `FAILED` |
| 3 | PDB path, relative to run dir | PDB path | PDB path |
| 4 | empty | PSF path, relative to run dir | PSF path |

Failures are first-class entries, not gaps: a failed job is a known outcome.

**Locking.** The local `Scheduler` fans jobs out to `Worker` **processes**
([libparallel.py:80-107](src/haddock/libs/libparallel.py#L80-L107)), so concurrent
appends come from separate OS processes. This requires an inter-process file lock —
`fcntl.flock` on an exclusive handle opened `O_APPEND` — not a `threading.Lock`.
Acquire, append, release.

**Duplicate rule.** The same job checksum appearing more than once with the *same*
result checksum is fine and expected. The same job checksum with a **different**
result checksum is a **reproducibility error**: raise at parse time, reporting both
entries. `FAILED` versus a successful checksum counts as a difference. This is
strict by design — a job key that maps to two different results means the key is
under-specified or the computation is nondeterministic, and both are bugs worth
surfacing loudly.

Suggested home: a new `src/haddock/libs/libcache.py` holding the record format,
locking, parsing, lookup and restore policy — kept separate from the Seamless
quarantine module.

---

## 6. The read side (`--cache`, opt-in)

```
haddock3 file.cfg --cache old-run-dir
```

`old-run-dir` is a *previous, different* run directory. `old-run-dir/CACHE` is
parsed once at startup into a lookup table (applying the duplicate rule above).
Then, per job, immediately before execution:

1. Compute the job checksum.
2. Look it up:
   - **Miss** → execute normally.
   - **Hit, `FAILED`** → copy the entry into this run's `CACHE` and raise
     `HaddockTaskExecutionError`, so the failure counts toward tolerance exactly as
     a real one would.
   - **Hit** → resolve fields 3/4 against `old-run-dir`, recompute their checksums,
     compare to field 2. Missing file or mismatch → log a message and **degrade to a
     miss**. Match → true hit → hardlink (or copy) into place.

The verification re-read is what makes this trustworthy: a `CACHE` file is a claim
about a directory that may have been edited, cleaned or partially deleted since.
Failing *open* is deliberate — a stale cache should cost time, not correctness.

Both mechanisms the failure path needs already exist:
`HaddockTaskExecutionError` ([core/exceptions.py:13](src/haddock/core/exceptions.py#L13),
with `CNSRunningError` as a subclass) is already caught per-task by `Worker.run`
([libparallel.py:96](src/haddock/libs/libparallel.py#L96)), and a missing output
already feeds the faulty count checked against `faulty_tolerance`
([modules/__init__.py:305-309](src/haddock/modules/__init__.py#L305-L309)).

**Restore targets come from the current job, never from the cached path.** Field 3
locates the *source* inside `old-run-dir`; the destination must be this job's own
`output_pdb_files`. Step numbering differs between runs — `2_rigidbody` in the old
run may be `3_rigidbody` or `02_rigidbody` in the new one — so reusing the cached
relative path as a destination would misfile the result.

On a true hit the new run's `CACHE` also gets a line, so a cached run can itself
serve as a future `--cache` source.

**CLI plumbing.** Add the flag to the parser at
[clis/cli.py:37-56](src/haddock/clis/cli.py#L37-L56) **and** to `main()`'s signature
— `cli()` calls `main(**vars(cmd))`, so the two must stay in step. Follow the
existing helper pattern (`add_restart_arg`, `add_extend_run`). The value must then
reach the jobs; today jobs learn their execution context only because
`SeamlessScheduler.__init__` stamps `execution_mode` on each task, and that scheduler
is being deleted, so this needs a deliberate path from `main()` through the workflow
into the module params and onto `CNSJob`.

> ### ⚠️ Contract: never modify the inode of a restored PDB
>
> A hardlinked cache hit shares an inode with the file in `old-run-dir`. Any
> **in-place** write through that link silently corrupts the previous run.
>
> `normalize_cns_pdb` rewrites via `Path.write_text`
> ([libcnsoutput.py:36](src/haddock/libs/libcnsoutput.py#L36)), which truncates the
> existing inode. It happens to be a no-op on a restored file — the cached content
> was already normalized before being checksummed, so it returns early — but that is
> far too thin a guarantee to rest on. **Normalization must be skipped outright on a
> cache hit.**
>
> Rename and `gzip_files(remove_original=True)` are safe: unlinking one link leaves
> the other intact. Any future code that writes to a step-output PDB in place must
> honour this contract. Copy remains the fallback where hardlinks are impossible
> (cross-device), and should be selected automatically on `EXDEV`.

**Also note:** `.out`/`.cnserr` will not exist on a hit. Scores come from the PDB's
REMARK lines via `HaddockModel(pdb.file_name)`, so nothing load-bearing breaks, but
anything assuming one log file per model needs checking.

---

## 7. Dependencies

`seamless-core` and `seamless-transformer` become **required runtime dependencies**,
since cache writing is unconditional. The remaining pieces of the current optional
extra in [pyproject.toml:74-82](pyproject.toml#L74-L82) — `hashserver`,
`seamless-database`, `seamless-remote`, `seamless-config`, `remote-http-launcher` —
are no longer needed at runtime. The `seamless-run` **executable** is needed only by
the synthesizer's differential tests, so it belongs in the dev/test extra, with those
tests skipping when it is absent.

This is a dependency change of the kind the PR checklist calls out; flagging it
explicitly rather than slipping it in.

---

## 8. Suggested build order

1. Remove seamless mode (§1), leaving `scan_cns_dependencies` and the bucket logic in
   place. Tests green with no caching at all.
2. Canonicalization (§2a) with its completeness assertion, plus unit tests over
   representative jobs from rigidbody (multi-molecule), emref (single, with
   `prev_ambig`) and topoaa (PDB + PSF).
3. In-process checksum (§2b, §2c).
4. Synthesizer (§3) and the differential test that binds it to step 3 — before any
   cache is written, so the key is trusted before anything depends on it.
5. Per-step manifest (§4).
6. CACHE write hook with locking and the duplicate rule (§5).
7. `--cache` read side (§6), the no-inode contract, and CLI plumbing.

---

## Verification

**Unit** (`tests/`)
- Canonical `.inp` for two rigidbody jobs docking different ensemble members is
  **bit-identical**; their job checksums differ only through input content.
- Canonical text contains no run-dir path, install path or step-folder name.
- An unclassifiable reference raises rather than falling back to `external/`.
- Job checksum is unchanged when the step folder is renumbered `2_` → `3_` → `02_`.
- `calculate_dict_checksum` output matches a golden hex value for a fixed dict
  (guards against a future hand-rolled JSON regression).
- One-PDB jobs produce a plain checksum; PDB+PSF jobs produce a deep one.
- CACHE round-trip: append under contention from multiple processes yields no torn
  lines; duplicate key with differing result raises; with identical result does not.
- Existing seamless tests removed or rewritten:
  `test_modules.py::test_get_engine_seamless`,
  `test_libparallel.py:189-204`, the `test_cnsjob_*_seamless_*` cases in
  `test_libsubprocess.py`, and the `get_analysis_exec_mode("seamless")` assertion in
  `test_modules_analysis.py`. The `scan_cns_dependencies` / `stage_cns_job` tests
  should be retained and retargeted at the canonicalization path.

**Differential** (`integration_tests/`, skipped without `seamless-run`)
- For representative jobs of each shape: synthesize, execute, and assert the
  transformation checksum **and** result checksum equal the in-process values. This
  is the test that keeps "as if `seamless-run`" honest.
- Execute a synthesized job and confirm CNS succeeds in the isolated workspace —
  validating the dependency scan.
- Update `integration_tests/conftest.py:9-23` (`--witness-cns-mode local|seamless`)
  and `test_witness_rigidbody.py` for the new topology.

**End-to-end**
```bash
# adapt examples/docking-protein-protein/docking-protein-protein-full-caching.cfg:
#   mode = "seamless"  ->  mode = "local"
haddock3 docking-protein-protein-full-caching.cfg          # run1-full, populates CACHE
haddock3 docking-protein-protein-full-caching.cfg --cache run1-full   # run2, should hit throughout
```
Assert: run2's CNS jobs are all hits; outputs are bitwise identical to run1's
(`integration_tests/witness_helpers.py` already has `_assert_artifacts_bitwise` and
`file_sha256`); run1's files are **unmodified** after run2 completes (the inode
contract); a `topoaa` step restores both `.pdb` and `.psf`. Then re-run with a step
inserted ahead of `rigidbody` and confirm hits persist despite renumbering. Finally
corrupt one cached PDB in run1 and confirm run2 logs the mismatch and recomputes.

**Lint / regression**
```bash
ruff check && pytest tests/ && pytest integration_tests/
```

Also update `CHANGELOG.md` (currently has no seamless or caching entries at all),
`docs/pages/testing/seamless-caching-overhead.md`, and
`docs/pages/testing/witness_test_class_system.md:378` (the G1 gate validates
`seamless-run` wrapping).

---

## Out of scope / known limitations

- **Only CNS jobs are cached.** Analysis modules, `openmm`, and pure-Python steps are
  untouched.
- **`.out`, `.cnserr` and `.seed` are not cached**, and are absent on a hit.
- **Non-local modes gain nothing.** `batch`, `mpi` and `grid` keep their current
  behaviour; caching is a local-mode feature.
- Unrelated but noted while surveying: `modules/defaults.yaml` `mode.choices` omits
  `mpi` and `grid` even though `get_engine` supports them, so a per-module
  `mode = "mpi"` fails validation. Not fixed here.

---

## Appendix A. Main-process cache-record writer

This appendix supersedes the per-worker cache-record append described in the
main body of this plan. It keeps CNS execution and cache restoration in worker
processes, but moves all cache-record writes to one dedicated thread in the main
process.

### Output contract and ownership

Before workers are started, each CNS job already has its expected output shape:
one PDB, or one PDB plus one PSF. The module constructs these expected paths and
passes them to `CNSJob`; the cache writer receives the same job objects and uses
those predeclared paths. It must not discover outputs from worker-local state.

There is exactly one writer thread for a scheduler invocation. It is the sole
appender to the current run's `CACHE` file, so the inter-process append lock is
removed. The writer serializes its own appends in thread order.

### Writer lifecycle

1. The scheduler starts the cache writer before starting worker processes and
   registers every cache-aware CNS job as outstanding.
2. The writer regularly runs a cycle over outstanding jobs. For each job, it
   checks the predeclared output paths. A job remains outstanding until all one
   or two declared output files exist. It commits a successful record only
   after that job's worker has reported completion, so it never checksums a PDB
   while CNS may still be writing it.
3. Before workers start, the main process scans every job's CNS input and
   computes the selected module/toppar/CNS invariant superset and its checksums
   once. It writes that union once to `CNS_DEPENDENCIES` and supplies the
   immutable checksum map to workers. Workers checksum only their job-specific
   CNS input and model dependencies. Once all declared outputs exist, a bounded
   pool owned by the writer normalizes the PDB with an atomic replacement (safe
   even when a cache hit restored a hardlink) and recomputes the result checksum
   from those bytes. The writer thread appends one successful record to `CACHE`
   and then removes that job from the outstanding set.
4. When all workers have finished (or the scheduler is otherwise shut down),
   the scheduler marks itself shut down. The writer observes that shared state
   and exits immediately, even if jobs remain outstanding. The module's
   `export_io_models()` performs one final synchronous writer cycle before it
   evaluates its normal missing-output tolerance. That final cycle records
   success for every newly complete output set and records `FAILED` for every
   still-outstanding job.

The final cycle is intentionally not a fixed visibility timeout. A job with an
absent output after workers have finished is classified as failed by the writer;
the module remains the owner of the existing tolerance decision.

### Worker behavior

- **Cache miss or unsuccessful cache hit:** the worker executes CNS exactly as
  normal. It does not append a cache record. The writer observes the resulting
  output files and records success when all declared outputs exist.
- **Full cache hit:** the worker restores the PDB (and PSF when declared) by
  copy or hardlink, then returns without appending a record. The writer is not
  told that this was a hit; it treats the appeared files as ordinary outputs,
  recomputes their result checksum, and appends the current-run success record.
- **`FAILED` cache hit:** the worker aborts the job immediately through the
  normal scheduled-task exception path. The writer records a current-run
  `FAILED` entry for that job, as if it had executed and failed.

Workers retain cache lookup and artifact restoration because those operations
decide whether CNS must run. They no longer append cache records, normalize a
restored hardlink, or impose a per-job output-visibility deadline.

Before workers start, the local scheduler reads every parsed source `CACHE`
index and identifies predeclared job PDB paths declared by a successful record
in any source. It does not read source artifacts or calculate checksums at this
stage. It runs that complete candidate batch first, distributing it across
workers, and starts the likely-miss batch only after every candidate worker has
finished. This is only a scheduling hint; normal cache checksum verification
remains authoritative.

Workers search source indexes in command-line order. A verified artifact stops
the search immediately. A `FAILED` record is provisional: it causes a scheduled
failure only if no later source supplies a verified artifact for the same job.

### Failure records and completion signalling

Each worker signals completion for every cache-aware job, including jobs whose
normal scheduled-task exception path caught a CNS failure. The writer uses this
signal only as a safe-to-checksum barrier; it does not need exception details.
During the final cycle, every job whose complete declared output set never
appeared receives one `FAILED` record. A task that has all outputs visible is
recorded as success, regardless of whether those files came from CNS or a
verified cache restore. The writer must never append both result states for the
same job checksum.

The `CACHE` format, canonical mapping, artifact verification, and current-run
relative paths remain unchanged. A successful CACHE record is valid only when
its PDB artifact is already normalized: it contains no CNS run-volatile header
lines. Older or otherwise stale cache artifacts that violate this contract are
cache misses; cache verification must not normalize them for compatibility.
Tests must cover one-PDB and PDB+PSF jobs, success, miss, verified hit,
unsuccessful hit, and `FAILED` hit under this single-writer protocol.
