# Native local-mode CNS caching: implementation handoff plan

## Status and authority

This document is the implementation handoff for the pre-Appendix A version of
`caching-redesign-plan.md`. It turns that design into repository-specific work,
settles the implementation details that the design left implicit, and defines the
tests and acceptance gates for each phase. If the two documents differ on an
implementation detail, use this document; retain the original document as design
rationale.

This is a plan only. Do not implement any part of it while reviewing or approving
this handoff.

## Outcome

After implementation:

- `mode = "seamless"` no longer exists. Local CNS jobs execute CNS directly, as
  they do in today's `mode = "local"` path.
- Every CNS job in a CLI-managed workflow that runs through the local scheduler
  writes a content-addressed result record to `<current-run>/CACHE`; no flag is
  required to populate it.
- `haddock3 workflow.cfg --cache <previous-run>` opts into reads from one different
  run. A verified hit restores the current job's PDB and optional PSF without
  launching CNS. A missing or corrupt artifact is a miss, never a reused result.
- Job identity is invariant to install/run location, step numbering and zero-fill,
  and per-run/per-job filenames, while remaining sensitive to every file CNS reads,
  the CNS executable, the canonical script, seed, count, and output shape.
- Normal execution imports only the Seamless checksum/transformation libraries. It
  does not run `seamless-init`, `seamless-upload`, `seamless-run`, or any Seamless
  service.
- An executable `seamless-run` synthesizer and differential integration tests prove
  that HADDOCK's in-process job and result checksums match the reference CLI.
- Each local CNS step exposes its invariant dependency set in
  `<step>/CNS_DEPENDENCIES`.

## Non-goals

- Do not cache analysis modules, OpenMM, or other pure-Python/external jobs.
- Do not add caching to `batch`, `mpi`, or `grid` execution.
- Do not cache `.out`, `.cnserr`, `.seed`, warnings, or other CNS by-products.
- Do not change run-directory naming, `io.json`, output model names, CNS scientific
  parameters, or the unrelated omission of `mpi`/`grid` from `mode.choices`.
- Do not turn `libseamless.py` into a general cache-policy module. Seamless API
  usage stays quarantined there; cache record/restore policy belongs in a new
  `libcache.py`.
- Do not import records from the retired service-backed Seamless cache. Existing
  configs using `mode = "seamless"` must migrate to `mode = "local"`; their first
  native run populates the new run-local CACHE format.

## Handoff decisions (no implementation choices remain open)

### User-facing behavior

1. Add `--cache RUN_DIR` only to the `haddock3` CLI. Resolve it to an absolute path
   before entering the new run directory.
2. The cache source must be an existing directory, must contain a regular `CACHE`
   file, and must resolve to a directory different from the current run. Reject an
   invalid source before workflow execution with a configuration/setup error.
3. Parse the source `CACHE` exactly once after `setup_run()` has supplied the
   current `run_dir` and before constructing `WorkflowManager`. `--setup` validates
   the source but neither creates a current `CACHE` nor prepares jobs.
4. `--cache` is only meaningful for local CNS steps. If the workflow's global mode
   is non-local, fail early instead of silently ignoring the flag. A local workflow
   may still contain a per-module non-local override; that step bypasses both cache
   reads and writes, while local CNS steps retain caching.
5. A source record whose artifact is absent, outside the source run, unreadable, or
   has the wrong checksum logs one warning containing the job checksum and reason,
   then executes CNS normally. Malformed/conflicting `CACHE` records are errors at
   startup, not misses.
6. Cache hits do not create `.out`, `.cnserr`, or `.seed` files. Existing consumers
   must tolerate their absence; scores continue to come from PDB REMARK records.

### Runtime context propagation

Do not use module globals or environment variables for cache state; Python 3.14's
multiprocessing behavior makes inherited mutable state an unsafe contract. Use this
explicit route:

```text
clis.cli.main(CacheContext)
  -> WorkflowManager
  -> Workflow
  -> Step
  -> BaseHaddockModule.cache_context
  -> get_engine(..., cache_context=...)
  -> local Scheduler
  -> CNSJob.cache_context
  -> worker process calls CNSJob.run()
```

- Define the optional `cache_context` attribute on `BaseHaddockModule`, defaulting
  to `None`, so standalone module tests and non-CLI callers remain compatible.
- Give `WorkflowManager`, `Workflow`, and `Step` explicit optional constructor
  parameters rather than mixing cache state into validated/saved module config.
- Pass the context to `get_engine` from every current CNS module call site:
  `topoaa`, `topocg`, `rigidbody` (both call sites), `cgtoaa`, `emref`, `flexref`,
  `mdref`, `emscoring`, and `mdscoring`. Non-CNS users of `get_engine` need no
  changes because the new argument defaults to `None`.
- Only the local `get_engine` branch forwards the context to `Scheduler`.
  Its partial also passes `cache_debug=params["debug"]`, because debug can be
  overridden per module. `Scheduler.__init__` assigns `cache_context` and
  `cache_debug` only to tasks exposing those attributes; do not import `CNSJob`
  into `libparallel.py` merely for a type check.
- `CNSJob` captures an absolute `work_dir` when constructed. This makes resolution
  of in-memory `.inp` text deterministic in multiprocessing workers. It defines
  `cache_context = None`, `cache_debug = False`, and `cache_hit = False` for the
  scheduler to populate.

### Cache types and ownership

Create `src/haddock/libs/libcache.py` with these responsibilities and these typed
dataclass names:

- `CacheRecord(job_checksum, result_checksum, pdb_path, psf_path)` represents all
  four tab-separated fields. `result_checksum == "FAILED"` is the only failure
  representation.
- `CacheIndex(source_run, records)` is the read-only-by-contract, pickleable result
  of parsing a source cache once. Workers never mutate its checksum-to-record dict.
- `CacheContext(current_run, source_index)` is pickleable and contains only
  absolute `Path` values and immutable records. Per-module debug state is kept on
  `CNSJob`, not here.
- `parse_cache`, `append_cache_record`, `lookup_cache_record`,
  `verify_and_restore`, compression-transparent artifact hashing, and debug-command
  append/locking live here.

Keep `CanonicalMapping`, dependency resolution/canonicalization, transformation
construction, result-checksum construction, staging, and command synthesis in
`src/haddock/libs/libseamless.py`. No other production module imports `seamless` or
`seamless_transformer`.

### CACHE format and validation

`<run>/CACHE` is UTF-8 text. Every line has exactly four tab-separated fields and a
trailing newline:

```text
<64-lowercase-hex-job>\t<64-lowercase-hex-result|FAILED>\t<relative-pdb>\t<relative-psf-or-empty>\n
```

- Paths use POSIX separators, are relative to the owning run, contain no tab or
  newline, and cannot be absolute or contain `..` after normalization.
- A success record has a non-empty PDB path and either an empty PSF path or one PSF
  path. A failure record retains the current job's expected PDB and optional PSF
  paths so all four fields are still present.
- Ignore one final empty line only. Reject blank interior lines, wrong field counts,
  uppercase/short/non-hex checksums, `FAILED` in field 1, unsafe paths, and records
  whose output arity disagrees with another record for the same job checksum. A
  zero-byte file is a valid empty cache; any non-empty file must end in `\n`, so a
  truncated last record is rejected.
- Duplicate job checksums with an identical result checksum are valid even when
  their source paths differ; retain the first record in file order after confirming
  equal output arity. The same job checksum with a different result
  checksum—including `FAILED` versus success—is a reproducibility error that
  reports both line numbers and records.
- Append with one `os.open(..., O_WRONLY|O_CREAT|O_APPEND, 0o644)` file descriptor
  and `fcntl.flock(LOCK_EX)`. Encode one complete record and loop on `os.write`
  until all its bytes are written, then unlock and close. Do not `fsync` each
  opportunistic cache record. This is an inter-process protocol; do not use
  `threading.Lock`.
- Do not rescan the growing file on every append. Duplicate validation occurs in
  the single linear parse when a run is selected as a source; this avoids quadratic
  write cost for workflows with thousands of jobs.

### Cleaned-run and restore semantics

`gear.clean_steps.clean_output()` replaces PDB/PSF outputs with `.gz` files. CACHE
paths nevertheless remain the logical uncompressed paths written when the job
completed. Therefore source artifact resolution must:

1. Try the recorded path.
2. If it is absent, try that exact path plus `.gz`, then `.zst`; both are supported
   by the pinned Seamless compression helper.
3. Hash compressed artifacts as their decompressed bytes, matching
   `seamless-run`; restore decompressed bytes to the current job's uncompressed
   destination.

For an uncompressed source, create a uniquely named temporary hardlink beside the
destination; on any hardlink `OSError`, remove the partial temporary and copy to a
new temporary instead. Hash the temporary artifact, not the source before linking,
to close the verification/restore race. For a compressed source, stream-decompress
to a temporary file and hash those bytes. For a two-output record, fully stage and
verify both temporary files before replacing either destination. Finish with
`os.replace`; clean all temporary files on miss/error.

Resolve the source with `strict=True` and require it to remain beneath
`CacheIndex.source_run`. Destinations always come from the current `CNSJob`'s
declared outputs and must remain beneath `CacheContext.current_run`; cached path
fields are source locators only.

A restored uncompressed artifact may share an inode with the old run. Cache hits do
not call output normalization: every native CACHE artifact was normalized before
its record was written, so normalizing again is both redundant and unsafe for a
hardlink. Delete the later module-level normalization in `BaseCNSModule`; output
normalization has exactly one owner, the successful direct-execution path in
`CNSJob.run`. Add a prominent contract comment at restore: no cache-restored shared
inode may ever be modified in place. Do not rely on today's early-return behavior
in `normalize_cns_pdb` as inode protection.

### Success and failure boundary

Wrap only the local CNS execution inside `CNSJob.run`; leave `BaseJob`/`Job`
unchanged.

1. When `cache_context is None`, preserve today's direct CNS subprocess,
   compression, and error behavior, but still apply the new mandatory output
   normalization postcondition.
2. Otherwise compute the canonical mapping and job checksum before lookup.
3. A source `FAILED` hit appends the corresponding current-run failure record and
   raises `HaddockTaskExecutionError`. `Worker.run` then records `None`, preserving
   module tolerance behavior.
4. A verified success hit appends a record using the current run's output paths,
   marks the job as a hit, and returns `b""` without CNS or normalization.
5. A miss executes the existing direct-CNS body. After CNS succeeds, always
   normalize every declared PDB output before any result checksum or CACHE append;
   this postcondition applies equally when no cache source was supplied. Require
   exactly one declared/existing PDB and zero or one declared/existing PSF. Missing
   declared outputs become a `CNSRunningError`, are recorded as `FAILED`, and flow
   through the existing worker/tolerance boundary.
6. Catch and record `HaddockTaskExecutionError` outcomes as `FAILED`, then re-raise.
   Do not cache unexpected programming/system exceptions as deterministic job
   failures; re-raise them unchanged so existing unexpected-error behavior remains
   visible.
7. Compute the successful result checksum from the bytes as they finally rest on
   disk and append the success record before returning.

Remove the `normalize_cns_output_pdb` module parameter and the
`normalize_output_pdb` `CNSJob` argument/attribute. Normalization is no longer an
optional output policy: all successful CNS-generated PDBs have one stable on-disk
representation regardless of whether cache reading is enabled.

## Canonical checksum specification

### One resolver, one mapping

Refactor the current `scan_cns_dependencies` resolution loop so one pass returns a
`CanonicalMapping` consumed by all of the following:

- dependency reporting;
- canonical `.inp` rewriting;
- in-process job checksum construction;
- `CNS_DEPENDENCIES` generation; and
- the `seamless-run` synthesizer.

The resolver must accept either a materialized `Path` or in-memory script text plus
the job's captured `work_dir`. Preserve recursive `@`/`@@` scanning, `$var`/`&var`
assignment resolution, optional guards, `MODULE:`/`TOPPAR:`, variable-to-variable
references, and dynamic `TOPPAR` prefixes. Retain `unresolved_reads` only for
genuinely optional/nonexistent candidates already tolerated by current behavior;
an existing read dependency that cannot receive a canonical role is a hard error.

`CanonicalMapping` must be immutable/pickleable and carry at least:

- canonical script text;
- original-to-canonical dependency paths and content checksums;
- canonical output names and current output destinations;
- output shape (PDB or PDB+PSF);
- the step-invariant canonical dependency names used by the manifest; and
- enough staging metadata for the synthesizer, without independently resolving
  paths a second time.

### Canonical names and classification

Use these exact stable names:

| Role | Canonical name |
|---|---|
| script | `canonical.inp` |
| CNS executable | `canonical-cns` |
| input PDBs, first reference order | `canonical-input-{i}.pdb` |
| input PSFs, first reference order | `canonical-input-{i}.psf` |
| ambiguous/unambiguous/H-bond restraints | `canonical-ambig.tbl`, `canonical-unambig.tbl`, `canonical-hbond.tbl` |
| dihedral/symmetry/tensor restraints | `canonical-dihe.tbl`, `canonical-symmetry.tbl`, `canonical-tensor.tbl` |
| CG-to-AA inputs by indexed CNS variable | `canonical-cg-input-{i}.pdb`, `.psf`, or `.tbl` |
| ligand additions | `canonical-ligand.top`, `canonical-ligand.param` |
| module source | `module/<relative path below this module's cns directory>` |
| force field | `toppar/<relative path below haddock/cns/toppar>` |
| CNS stdout | `canonical.out` |
| result | `canonical-output.pdb`, optionally `canonical-output.psf` |

Recognize named roles by resolved path plus the variable that introduced it
(`ambig_fname`/derived `filenam0`, `unambig_fname`, `hbond_fname`,
`dihe_fname`/`dihe_f`, `symtbl_fname`, `tensor_tbl`, `ligand_top_fname`,
`ligand_param_fname`, and indexed `input_aa_*`/`input_cgtbl_*`). Map repeated
references to the same canonical path. Built-in topology/parameter/link files stay
under `toppar/`; do not misclassify their `*_infile` variables as per-job inputs.

Classify input PDB/PSF references by CNS read context (`coor @@...`,
`structure @@...`) and first-emission order, not basename. If a current CNS module
uses another run-scoped read role, add it to this explicit registry with a test;
never recreate the current `external/<absolute path>` fallback.

Rewrite every resolved occurrence in `@`/`@@` reads and assignment values. The
same original path always receives the same replacement. Preserve all other script
bytes, including seed and count. Assert that the final canonical text contains none
of:

- the absolute current run path;
- the absolute installation/module/toppar paths;
- unresolved path-bearing occurrences of the original per-job input/output names;
  or
- a step-folder token matching HADDOCK's numbered-step regex.

The assertion must name the leaked token and job on failure.

### Compression-transparent input checksums

The inspected pinned APIs are `seamless-core 0.1.4` and
`seamless-transformer 0.6.1`. Pin those exact versions in runtime dependencies
because transformation construction uses semi-private API.

Inside `libseamless.py`, provide one file-content helper matching
`seamless-run` behavior:

- uncompressed files use
  `seamless.checksum.calculate_checksum.calculate_file_checksum` (streaming);
- `.gz`/`.zst` files are decompressed according to
  `seamless_transformer.compression_utils.strip_compression_suffix` and
  `decompress_bytes`, then passed to `calculate_checksum`;
- the canonical filename omits the compression suffix, so compressed and
  uncompressed representations have the same input identity.

Do not claim that `calculate_file_checksum` itself decompresses—it does not in
`seamless-core 0.1.4`. Keep this compatibility detail covered by a unit test.

### Transformation and result checksums

Call
`seamless_transformer.cmd.bash_transformation.prepare_bash_transformation(...,
dry_run=True)` with the canonical checksum dictionary. Pass empty environment/meta,
no metavariables, canonical paths as literal wrapper arguments, and
`make_executables=["canonical-cns"]`.

The literal wrapper first executes CNS, then unconditionally removes lines beginning
`REMARK FILENAME=`, `REMARK initial structure `, and `REMARK DATE:` from
`canonical-output.pdb` through a temporary file and atomic rename, exactly matching
`libs.libcnsoutput.normalize_cns_pdb`. This ensures the reference result checksum
describes the same final bytes as direct execution. Unit-test the filter against
`normalize_cns_pdb`, including final-newline behavior.

- PDB-only: use no result targets, capture stdout, redirect CNS stdout to
  `canonical.out`, normalize, then `cat canonical-output.pdb`. The
  transformation output is `("result", "bytes", None)`.
- PDB+PSF: declare both canonical outputs as result targets, do not use the PDB-only
  `cat`, normalize the PDB before capture, and produce
  `("result", "deepfolder", None)`.

Return the transformation checksum as the job checksum. Assert one PDB and at most
one PSF before constructing either shape.

For successful results:

- PDB-only is the compression-transparent SHA-256 of the final PDB bytes.
- PDB+PSF is
  `calculate_dict_checksum({"canonical-output.pdb": pdb_sha,
  "canonical-output.psf": psf_sha})`.

Never reproduce Seamless's plain-cell JSON serialization locally.

## Reference synthesizer and observability

Retain staging helpers in `libseamless.py`, but rewrite them around the shared
`CanonicalMapping`. `synthesize_seamless_run(job, mapping, stage_dir)` must emit a
complete runnable workspace and argv matching the exact one-output/two-output
transformation shapes above. It is test/reference code called in production only
when `debug=true`; ordinary cache reads/writes do not stage jobs.

For debug mode:

- stage beneath `<step>/.cache-stage/<job-checksum>/` and retain the directory;
- make stage creation process-safe with a stable `.cache-stage/.lock`: under lock,
  build a unique temporary sibling and rename it to the checksum directory; if an
  already-complete checksum directory exists, discard the temporary and reuse it.
  Never expose a partially staged directory, and retain the lock file;
- after the job outcome is known, append its shell-quoted command to
  `<run>/cached-commands.sh` under the same `fcntl` locking discipline as CACHE;
- write `#!/usr/bin/env bash` exactly once, make the file executable, and precede
  each command with `# job=<hex> result=<hex|FAILED>`;
- a cache hit is included too, so the file is a complete reference for the jobs the
  run considered; and
- non-debug runs create neither `.cache-stage` nor `cached-commands.sh`.

The per-step `<step>/CNS_DEPENDENCIES` contains a sorted, unique, newline-terminated
list of only the mapping's invariant canonical names:
`canonical-cns`, `module/...`, and `toppar/...`. It contains no checksums, absolute
paths, job inputs, outputs, or per-job headings. Multiple worker processes update a
union safely using a separate stable `<step>/.cns-dependencies.lock`: lock, read the
current set, union, write a unique sibling temporary, `os.replace`, unlock. Retain
the zero-byte lock file for the life of the step; its presence must not affect
manifest contents or caching.

## Implementation phases

Each phase ends with the named focused tests passing. Keep changes incremental;
do not begin cache reuse until differential tests trust the checksum.

### Phase 1 — remove Seamless execution mode

Change:

- `src/haddock/modules/defaults.yaml`: remove `seamless` from choices and rewrite
  mode help for local/batch only.
- `src/haddock/modules/__init__.py`: remove `SeamlessScheduler` import, Literal
  member, engine branch, and available-engine label.
- `src/haddock/clis/cli.py`: remove the lazy import and
  `initialize_seamless_run` call.
- `src/haddock/modules/base_cns_module.py`: make `cns_input_as_file()` depend only
  on `debug`; delete its `export_io_models` normalization override,
  `normalize_cns_output_pdbs`, and now-unused `normalize_cns_pdb` import.
- `src/haddock/modules/defaults.yaml`: also delete the
  `normalize_cns_output_pdb` option because CNS output normalization is mandatory.
- `src/haddock/modules/analysis/__init__.py`: remove `seamless` from the local
  downgrade tuple.
- `src/haddock/libs/libsubprocess.py`: delete `execution_mode`, Seamless staging
  imports, dispatch, and `_run_with_seamless`; keep the direct CNS body.
- `src/haddock/libs/libseamless.py`: delete `initialize_seamless_run`,
  `SeamlessScheduler`, upload/sidecar state and helpers. Retain dependency resolver
  and staging primitives for later refactoring.
- `src/haddock/modules/sampling/rigidbody/__init__.py`: keep the existing
  `mode != "local"` sequential-input condition. Once seamless is gone, native local
  caching correctly uses the in-memory parallel preparation path.
- All CNS module constructors: remove existing `normalize_output_pdb=...` arguments;
  `CNSJob` now owns mandatory normalization. Topology's PDB+PSF constructors need no
  special case.
- Delete/replace the Seamless-mode assertions in `tests/test_modules.py`,
  `tests/test_libparallel.py`, `tests/test_libsubprocess.py`, and
  `tests/test_modules_analysis.py`.
- Update `tests/test_libcnsoutput.py` and `tests/test_module_rigidbody.py` to remove
  normalization opt-out coverage/fixtures and prove context-free successful
  `CNSJob` execution always normalizes its declared PDB outputs.

Gate: `ruff check` and the affected unit tests pass with CNS computation/error
behavior unchanged, mandatory stable PDB output, and no runtime reference to
`mode='seamless'`.

### Phase 2 — canonical dependency mapping

First update `pyproject.toml`: move exactly `seamless-core==0.1.4` and
`seamless-transformer==0.6.1` into `[project].dependencies`, then remove the
obsolete `seamless` extra and its hashserver/database/remote/config/launcher
packages. The transformer distribution supplies the reference CLI; differential
tests still skip when `seamless-run` is not on `PATH`.

Then change `libseamless.py` to introduce `CanonicalMapping`, shared resolution and
rewrite, role classification, completeness assertions, compression-transparent
file checksums, and step-manifest updates. Update `CNSJob` to capture `work_dir`,
but do not read or write CACHE yet.

Move the retained scan/stage unit coverage from `tests/test_libsubprocess.py` into a
new `tests/test_libseamless.py`; leave subprocess behavior tests where they are.
Build fixtures for:

- rigidbody with two PDBs, two PSFs, and per-job AIR;
- emref with one structure and `previous_ambig`;
- topoaa/topocg with one PDB and one PSF output;
- cgtoaa indexed PDB/PSF/TBL inputs; and
- named unambiguous, H-bond, dihedral, symmetry, tensor, and ligand files.

Gate: canonical text is byte-identical across run roots, renamed inputs, and step
folders `2_...`, `3_...`, and `02_...`; only dependency content changes alter the
input tree; every current CNS read variable is classified; leakage/unclassifiable
references fail loudly; compressed/uncompressed copies hash identically; manifest
contents are sorted, stable, and job-input-free.

### Phase 3 — trusted in-process checksums and synthesizer

Add `job_checksum`, `result_checksum`, transformation construction, canonical
staging, wrapper/manifest emission, `synthesize_seamless_run`, and debug artifact
support to `libseamless.py`.

Create `integration_tests/test_cns_cache_checksums.py`, guarded independently by
the availability of CNS and `seamless-run`. For representative PDB-only rigidbody
and PDB+PSF topoaa jobs:

1. Build one `CanonicalMapping`.
2. Feed that exact object to both in-process construction and synthesis.
3. Derive a reference dry-run argv by adding `--dry --write-job <temp-job-dir>` to
   the synthesized options, run it, and compare the sole checksum printed on stdout
   with the in-process transformation checksum.
4. Execute the unmodified synthesized invocation with `-vv` in its isolated
   workspace, parse Seamless's `Result checksum:` diagnostic, and compare it with
   the in-process result checksum and the checksum recomputed from restored files.
5. Assert expected output shape and CNS success. The isolated workspace contains
   only mapping-declared inputs, so this also proves dependency completeness.

Also unit-test the fixed deep-checksum golden value, output-arity rejections, no
metavariables, and exact generated wrapper/argv including unconditional
normalization.

Gate: both differential shapes pass. Do not proceed on the basis of unit tests
alone; without the executable reference proof, cache writes are not trusted.

### Phase 4 — explicit runtime plumbing

Change:

- Create `src/haddock/libs/libcache.py` with `CacheRecord`, `CacheIndex`,
  `CacheContext`, strict `parse_cache`, lookup, source/current path validation, and
  `add_cache_arg(parser)` following the existing restart/extend helper pattern.
- `src/haddock/clis/cli.py`: call that helper, add
  `main(cache: Optional[FilePath] = None)`, then perform validation, one-time source
  parse, and `CacheContext` construction. Construct a context for every CLI-managed
  workflow even without `--cache` (`source_index=None`), because writes are always
  enabled for any CNS step whose effective mode is local.
- `src/haddock/libs/libworkflow.py`, `src/haddock/modules/__init__.py`, and all CNS
  module engine call sites listed under Runtime context propagation: implement the
  explicit context route.
- `src/haddock/libs/libparallel.py`: accept/stamp optional context and the
  per-module `cache_debug` value only for local tasks that expose them.
- `tests/test_cli.py`, `tests/test_modules.py`, `tests/test_libworkflow.py` (or the
  existing workflow test file), and `tests/test_libparallel.py`: cover the parser,
  invalid/same source, one-time parse, propagation, defaults, and non-CNS
  compatibility.

Gate: context reaches a multiprocessing `CNSJob` under both `fork` where available
and the platform default start method without entering `params.cfg` or module
configuration validation.

### Phase 5 — CACHE write side

Extend `libcache.py` with record serialization/append and locking, and wrap local
`CNSJob.run` with mapping/checksum plus write behavior. Implement manifest union
and debug-command append now that outcomes exist.

Add `tests/test_libcache.py` for format validation, safe paths, exact round-trip,
same/different duplicates, `FAILED`, and many processes appending concurrently.
Extend `tests/test_libsubprocess.py` for success, expected CNS failure, missing
PDB/PSF, unexpected exception non-caching, unconditional normalization with and
without a source index, debug/non-debug artifacts, concurrent idempotent debug
staging, and one/two-output records. Update `tests/test_libcnsoutput.py` to remove
the opt-out case and prove that a successful `CNSJob` always normalizes (the focused
test begins in Phase 1 and remains a regression gate here).

Gate: every successful or expected-failed local `CNSJob` with context produces one
valid record; contention produces no torn/lost lines; non-local/context-free jobs
produce none.

### Phase 6 — opt-in read side and inode safety

Implement lookup, verified staging/restore, compressed-source support, `FAILED`
replay, hit recording, logging, and normalization guards. Tests must cover:

- miss executes CNS;
- exact verified hit does not invoke `subprocess.Popen`;
- `.pdb.gz` and `.psf.gz` sources restore decompressed outputs;
- missing, corrupt, unreadable, and escaped source paths degrade to a miss;
- PDB+PSF verification is all-or-nothing;
- `FAILED` replay is caught by `Worker` and counts toward module tolerance;
- destinations come from the current job despite different old step/output paths;
- an uncompressed hit uses a hardlink where supported and `EXDEV` uses copy;
- running module export and cleanup after a hit leaves the source artifact
  byte-for-byte unchanged and does not invoke normalization; and
- a hit-bearing run can be used as the next source.

Gate: all unit tests plus a local module-level topoaa (PDB+PSF) and rigidbody
(PDB-only) cache round trip pass.

### Phase 7 — integration, examples, and documentation

Change:

- `integration_tests/conftest.py`: remove `--witness-cns-mode`; witness tests always
  use direct local CNS execution, while cache round trips live in the dedicated
  cache integration test below.
- `integration_tests/test_witness_rigidbody.py`: remove service/cache environment
  prerequisites and exercise native local caching/differential synthesis.
- `examples/docking-protein-protein/docking-protein-protein-full-caching.cfg`: set
  `mode = "local"` and explain the second-run `--cache` command in comments.
- `docs/pages/testing/seamless-caching-overhead.md`: preserve historical benchmark
  results but clearly mark the old execution mode as superseded; document native
  checksum overhead and the differential reference role.
- `docs/pages/testing/witness_test_class_system.md`: redefine G1 around synthesized
  reference equivalence rather than runtime wrapping.
- `docs/pages/architecture.md` and user-facing CLI documentation: document CACHE,
  `CNS_DEPENDENCIES`, local-only scope, integrity fallback, cleaned-run support, and
  missing logs on hits.
- `CHANGELOG.md`: add removal of seamless mode and native local CNS caching, calling
  out the two newly required runtime dependencies.

Add `integration_tests/test_cns_cache.py` using a small deterministic config rather
than the 1000-model example. It must:

1. Runs once and populates CACHE.
2. Runs a second directory with `--cache` and asserts all eligible jobs hit and
   outputs are bitwise identical.
3. Confirms the first run did not change.
4. Repeats after inserting a preceding step/altering zero-fill and still hits.
5. Cleans/compresses the source and still hits.
6. Corrupts one source artifact and observes exactly that job recompute.
7. Restores topoaa PDB+PSF together.

Gate: documentation no longer presents `mode="seamless"` as valid, the example is
current, and the native cache integration scenario passes.

## Verification matrix

Run focused tests after each phase, then the repository loop:

```bash
ruff check
pytest tests/
pytest integration_tests/
pytest end-to-end_tests/
```

Tests requiring CNS keep the repository's existing CNS-availability skip. Tests
requiring the reference executable add an independent `seamless-run` skip; cache
format, restore, locking, canonicalization, and CLI tests must not require services,
network, or CNS.

Final acceptance requires all of the following:

- `rg` finds no production mode branch, help text, or initialization/upload path
  for seamless execution.
- Under `src/haddock`, only `libseamless.py` imports Seamless Python packages.
- A normal local run writes CACHE without `--cache` and launches no Seamless
  executable/service.
- The source cache is parsed once; worker lookups use the immutable index.
- PDB-only and PDB+PSF checksums match executable synthesized references.
- Renames, absolute path changes, step insertion, and zero-fill changes do not
  change job identity; content/seed/count/output-shape changes do.
- Every current CNS dependency is classified or rejected, and each local CNS step
  has a deterministic `CNS_DEPENDENCIES` file.
- Every reuse verifies source bytes, supports cleaned `.gz` artifacts, restores to
  current destinations, and fails open to execution on artifact problems.
- Conflicting duplicate keys fail loudly; process contention cannot tear CACHE or
  debug-command lines.
- Failure hits follow existing tolerance semantics.
- Restoring, exporting, and cleaning a hit never normalizes the restored hardlink or
  changes the previous run's inode contents.
- Non-local modes and context-free/standalone uses retain their old behavior and do
  not create cache artifacts.
- Unit, integration, end-to-end, and lint suites pass, with only environment-based
  CNS/reference skips.

## Risks and mitigations

| Risk | Mitigation / proving test |
|---|---|
| Missed CNS read produces an under-specified key | One shared resolver, hard error for unclassified existing reads, per-step manifest, isolated synthesized CNS execution |
| Private Seamless API drift | Exact dependency pins plus transformation/result differential tests |
| Path/name leakage causes false misses | Canonical role names, completeness assertions, renumber/relocation tests |
| Cleaned cache appears empty | Logical paths plus compression-transparent source fallback, hashing, and decompressed restore |
| Concurrent workers corrupt CACHE/manifest/debug script | `fcntl` lock protocols and multiprocess contention tests |
| Stale or edited source returns bad science | Verify staged bytes before install; degrade artifact problems to a miss |
| Hardlink lets new run mutate old run | Normalize only after direct CNS execution, never on restore/export; temp-link/atomic install and source-unchanged integration assertion |
| Cached failure bypasses tolerance | Re-raise `HaddockTaskExecutionError` through existing `Worker` boundary |
| Cache state disappears under multiprocessing spawn/forkserver | Explicit pickleable context propagation; no inherited globals |
| Debug reference drifts from production identity | The same immutable `CanonicalMapping` feeds both consumers |

## Handoff-readiness audit

Review cycle completed before handoff:

1. Pass 1 added the explicit runtime route, strict CACHE schema, cleaned `.gz`/`.zst`
   restore, current-output targeting, phase gates, and binary final acceptance.
2. Pass 2 removed remaining deferred choices: per-module debug now travels on the
   job, duplicate-path behavior and append durability are fixed, dependency changes
   precede imports, lock-file lifetime is explicit, and CLI/cache types have exact
   homes and names.
3. Pass 3 made normalization an unconditional `CNSJob` postcondition, aligned
   synthesized and native final bytes, removed the later module-level write hazard,
   and specified process-safe debug staging. A final scan found no TBD/TODO or
   unassigned material decision.

The plan is ready to hand off only while every row remains satisfied:

| Criterion | Evidence in this plan | Status |
|---|---|---|
| Desired user-visible outcome and non-goals are explicit | Outcome; Non-goals | satisfied |
| Repository baseline and affected symbols/files are identified | Runtime route and Phases 1–7 | satisfied |
| Architecture boundaries and data flow are decided | Cache types and ownership; context diagram | satisfied |
| Formats, invariants, errors, and edge cases are specified | CACHE format, cleaned restore, failure boundary, checksum specification | satisfied |
| No material implementation choice is deferred | Handoff decisions fixes names, versions, routes, locks, restore and mandatory normalization postcondition | satisfied |
| Work is ordered by dependency with intermediate gates | Phases 1–7 | satisfied |
| Every behavior has proportionate automated verification | Phase gates and Verification matrix | satisfied |
| Final acceptance is observable and binary | Final acceptance checklist | satisfied |
| Risks, compatibility, docs, dependencies, and out-of-scope work are covered | Risks table, dependency phase, docs phase, Non-goals | satisfied |
| Another implementer can start without rediscovery | Exact files, APIs, types, call sites, fixtures, commands, and acceptance gates are named | satisfied |

Stop the readiness-review cycle when all rows are `satisfied`. If implementation
reveals a new unresolved design decision, update the relevant decision and tests in
this plan before continuing; do not silently choose behavior that changes these
contracts.
