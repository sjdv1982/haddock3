# Seamless CNS Caching Overhead Notes

**BLUF:** The current `seamless-run` subprocess overhead for a cached CNS job is
about 800 ms per job. The measurements suggest this could plausibly be reduced
to about 150 ms by avoiding per-job Python startup/import/shutdown and by adding
a fast path for the tiny generated buffers used for metavars and bash code.

This note records the overhead measurements from the exploratory HADDOCK3
Seamless CNS integration work. The goal was not to benchmark CNS itself, but to
understand the fixed cost of wrapping CNS jobs with `seamless-run`, especially
when the transformation result is already cached. This matters because the
expected production use case for HADDOCK3 is many small-to-medium CNS jobs. If a
cache hit still costs close to a second per model, expanding witness-oriented
tests to larger examples remains unnecessarily expensive even when CNS itself is
not re-executed.

The main test case was the protein-protein example
`examples/docking-protein-protein/docking-protein-protein-test.cfg`, configured
with `mode = "seamless"` and `ncores = 10`. The rigid-body module generated 10
CNS jobs. The cache directory used during the measurements was
`/home/agent/seamless-hadddock-cache`. A clean warmup run was important: an
earlier run with `--no-download` had left zero-byte `0_topoaa` PDB/PSF files,
which made subsequent rigid-body CNS jobs fail before writing PDBs. After
rebuilding from scratch with result materialization enabled, the rigid-body
PDBs were real coordinate artifacts, for example `rigidbody_1.pdb.gz` was about
37 KB and contained normal HADDOCK score remarks.

## Observed Timings

The ordinary `seamless-run` subprocess path had a no-work or near-no-work floor
around 0.75 to 0.85 seconds per CNS job. In earlier dry-run/upload tests, the
values varied enough that differences below roughly 100 ms should not be
overinterpreted. Submitting a real transformation rather than the dry/upload
path added only a small amount when the transformation was a cache hit or when
CNS execution was otherwise not part of the measured path. The observed
difference was on the order of 50 ms. In other words, the subprocess path is
dominated by the cost of starting Python, importing Seamless modules, setting up
configuration, and shutting down, not by the difference between a dry
transformation construction and a real cache-hit transformation submission.

To separate startup/import/shutdown cost from the internal work of
`seamless-run`, an experimental in-process call into
`seamless_transformer.cmd.api.main._main` was used. This was explicitly
temporary instrumentation: `_main` mutates process-global state such as
environment variables, working directory, and Seamless configuration, so the
HADDOCK Seamless scheduler had to be serialized to avoid concurrent corruption.
With that caveat, the warmed cache-hit rigid-body jobs were reduced to roughly
0.20 to 0.45 seconds each:

```text
mean   0.2427 s/job
median 0.2188 s/job
min    0.1978 s/job
max    0.4466 s/job
n      10 jobs
```

The transformation checksum set matched the subprocess warmup exactly. The
phase log showed 10 remote-cache hits, 0 misses, and no `execute_bash.*` events.
That is the key validation: the in-process run was measuring the overhead of a
real cached CNS transformation, not an artificial dry path and not a broken
placeholder PDB path.

These numbers support the rough conclusion that about 0.5 to 0.6 seconds of the
normal `seamless-run` CNS-wrapper overhead is process startup, import,
configuration startup, and shutdown. That portion can in principle be elided by
not invoking `seamless-run` as a new subprocess for every CNS job.

## Remaining In-Process Cost

After startup/import/shutdown was removed, the remaining overhead was roughly
0.22 to 0.25 seconds per cache-hit CNS job. The detailed phase log showed that a
large fraction of this remaining cost came from building and registering the
small transformation argument buffers:

```text
prepare_bash_transformation  mean ~0.122 s/job
prepare.serialize_new_args   mean ~0.117 s/job
```

The `prepare.serialize_new_args` label referred to the following block in
`seamless_transformer/cmd/bash_transformation.py`. The timing instrumentation
has since been removed, so this snippet preserves what the label meant:

```python
bashcode = prepare_bash_code(
    code,
    make_executables=make_executables,
    result_targets=result_targets,
    capture_stdout=capture_stdout,
    meta_variable_names=meta_variable_names,
)
new_args["code"] = ("text", None, bashcode)

for k, v in new_args.items():
    celltype, subcelltype, value = v
    buffer = serialize(value, celltype)
    checksum_hex = register_buffer(buffer, dry_run=dry_run)
    vv = celltype, subcelltype, checksum_hex
    transformation_dict[k] = vv
```

The name `serialize_new_args` is slightly misleading for this profile. The
actual serialization of the small argument values was essentially free, around
0.05 to 0.13 ms per argument. The time was spent in registering the serialized
buffers:

```text
register.asyncio_run_write   mean ~8.95 ms/write
90 writes total for 10 jobs
```

Each CNS job used 9 small generated buffers: 8 metavars plus the generated bash
code buffer. The per-job mean contribution was approximately:

```text
META__JOB_DIR        14.27 ms
META__EXITCODE_FILE  13.95 ms
META__MODULE_DIR     13.53 ms
META__CNS_EXEC       13.47 ms
META__TOPPAR_DIR     13.35 ms
META__STDERR_FILE    12.93 ms
code                 11.19 ms
META__STDOUT_FILE     9.39 ms
META__INPUT_FILE      9.04 ms
```

Combined, these small-buffer registrations account for about 115 to 120 ms per
cache-hit CNS job. This is about half of the remaining in-process overhead. The
rest is spread over file checksum loading, argument typing and file mapping,
cache lookup, result resolution, result materialization bookkeeping, and normal
HADDOCK staging/cleanup around the Seamless call. For reference, the warmed
cache-hit path showed approximately:

```text
compute_transformation_sync  mean ~18.8 ms/job
remote DB query              mean ~5.7 ms/job
remote result resolution     mean ~3.7 ms/job
get_result_buffer            mean ~5.2 ms/job
get_results                  mean ~26.7 ms/job
```

## Interpretation

The practical interpretation is:

1. A normal per-job `seamless-run` subprocess currently costs about 800 ms even
   when no CNS work is performed.
2. Most of that cost, roughly 500 to 600 ms, is not inherent to transformation
   construction or cache lookup. It is process startup, imports, configuration,
   and shutdown.
3. An in-process or daemonized path can reduce the per-job cache-hit overhead to
   about 250 ms, but the direct `_main` call used for measurement is not itself
   production-ready because of process-global side effects.
4. About half of the remaining 250 ms is registration of tiny generated
   argument buffers: the 8 metavars and the bash code buffer.
5. That tiny-buffer registration cost is plausibly optimizable with a fast path
   analogous to the checksum-sidecar strategy used for CNS dependency files.

The existing dependency sidecar optimization is conceptually different from
adding sidecar syntax to `seamless-run` input lists. The input manifest still
contains real file paths such as `file.ext` or `dir/`; the sidecar is an
implementation detail, for example `file.ext.CHECKSUM` or `dir.INDEX`, used to
avoid rereading and reuploading stable content. The same principle could be
applied to generated small buffers: avoid paying a per-buffer asynchronous
write path when the content is known, tiny, stable within a job construction,
and cheap to fingerprint directly.

## Possible Follow-Up Designs

One possible production design is to split transformation construction from
transformation execution. A HADDOCK-side integration could build or retrieve the
transformation checksum without launching a fresh `seamless-run` process, then
invoke a lower-level `seamless-run-transformation` or equivalent execution API
on that checksum. This would preserve the content-addressed cache contract while
removing the repeated CLI startup/import cost. A persistent worker process or a
small daemonized runner would be safer than direct concurrent `_main` calls,
because it could own Seamless global state explicitly and process jobs
sequentially or with controlled isolation.

A second optimization target is the generated metavars and code buffer. The
current path serializes and registers each one separately. The profile suggests
that grouping these generated buffers, using a direct local-cache registration
fast path, or avoiding remote write calls for values already known to be
materialized locally could remove a large fraction of the remaining overhead.
Even a conservative optimization that removes only half of `serialize_new_args`
would save roughly 60 ms per CNS cache hit. Removing most of it would bring the
cache-hit overhead closer to 120 to 150 ms before addressing HADDOCK-side
staging and result handling.

The key caution is that these optimizations should not weaken the dependency
contract. The transformation checksum must remain invariant between subprocess
and optimized paths. During the measurement, the subprocess warmup and
in-process cache-hit run produced the same transformation checksum set, which is
the standard that any future optimized runner should preserve.
