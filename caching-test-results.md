# CNS caching: first run of the contract test set

Results of running `end-to-end_tests/caching/` against the caching
implementation on `test-system-and-caching-pilot`, on 2026-08-31.

This is a **snapshot of an implementation that is being rewritten**, not a
verdict on the design. The taxonomy in `caching-use-cases.md` is the
specification; the suite is the instrument; this is one reading.

```
Phase 0 (instrument)      7 passed   7 failed
Phase 2 (the test set)   83 passed  57 failed  24 skipped (recorded scope boundaries)
```

Every finding below was reproduced by hand, outside the harness, with two
ordinary `haddock3` invocations.

> **One measurement caveat, learned the hard way.** A reused result is
> delivered as a hardlink, and a hardlink cannot cross a filesystem boundary,
> so a run whose directory is on a different device from the cache source gets
> a *copy* — indistinguishable from a recomputation to any inode-based check.
> Two of my own hand experiments produced a confident, entirely wrong "0 of 12
> reused" before I noticed. Phase 0's P0.3 exists precisely to make that
> failure loud, and it currently does not.

---

## Findings, in order of severity

### 1. Refinement scripts encode how many models are in their step

`flexref_1.inp` contains `eval ($mol_fix_origin_N=false)` and
`eval ($mol_shape_N=false)` for **every model index in the step**: with six
models selected there is a `_6` line, with five there is not. Every refinement
job's script therefore depends on how many *siblings* it has.

Demonstrated: with `min_population` changed from 2 to 1, four of the six
selected structures were byte-identical **and under the same filename at the
same rank** — and every refinement job was recomputed.

This is the single most expensive defect found. It drives the whole Axis 4 and
Axis 5 cluster: `seletop select`, `seletopclusts top_models`,
`sampling_factor`, any clustering change, any change to the ensemble, and
every composed case that touches selection. It means **any change to how many
models reach refinement throws away the entire refinement stage**, which is
exactly the reuse the feature exists to deliver.

Related, and the same shape: `axis5.8` shows the intended behaviour precisely.
Ranking clusters by size rather than score leaves all six structures
byte-identical under different cluster numbers (`cluster_2_model_1` becomes
`cluster_1_model_1`, and so on). The suite resolves that mapping correctly and
reports six false misses.

### 2. Orchestration parameters are written into the CNS script

`rigidbody_1.inp` begins:

```
eval ($ncores=4)
eval ($max_cpus=true)
eval ($mode="local")
eval ($batch_type="slurm")
eval ($queue_limit=100)
eval ($concat=1)
eval ($self_contained=false)
eval ($clean=false)
eval ($offline=false)
eval ($debug=true)
```

Changing `ncores` from 4 to 2 recomputes all eight jobs. So do `debug`,
`clean`, `offline` and `tolerance`. `max_nmodels` behaves the same way.

**The cache is behaving correctly here** — the script genuinely differs. The
defect is upstream, and it is the one the taxonomy already names: build the
CNS parameter set by *explicit inclusion* rather than by copying the module
parameters and deleting the ones known to be irrelevant.

### 3. Two existing features are broken outright, with no `--cache` involved

Both abort the run with a canonical-script assertion:

| Feature | Error |
|---|---|
| `cgtoaa` (any coarse-grained workflow) | `Canonical CNS script leaked step-folder token '/0_topoaa'` |
| per-model restraints (`ambig_fname` = a `.tgz`) | `Canonical CNS script leaked step-folder token '/4_flexref'` |

Neither needs `--cache`: the canonical mapping is built for every local CNS run
in order to write the `CACHE` file, so the assertion fires unconditionally.
This is a regression against plain HADDOCK3 usage, not a caching limitation —
`examples/docking-protein-protein-shape` cannot run. It cost the corpus its
whole coarse-grained base run, so the `topocg`/`cgtoaa` job shapes are
currently untested (recorded as a coverage hole, not as a pass).

A third instance of the same class — a restraint file named `ambig.tbl`
colliding with its own canonical name `canonical-ambig.tbl` — is fixed in
commit `5cd7f4d09`; it had made `examples/docking-protein-glycan` unrunnable.

### 4. A different installation path changes every job checksum

Running the identical workflow against a copy of the HADDOCK3 tree at another
path produces different job checksums for every job, so **reinstalling
invalidates every cache in existence**. The force field, the CNS templates and
the executable are byte-identical; only the directory holding them moved.

This also accounts for the Axis 6.16 cases, which shadow the install in order
to edit one template: they cannot show a precise partition while the install
path itself is in the key.

### 5. Renaming an input molecule invalidates the docking jobs

The topology jobs correctly follow the rename (they hit, and the suite asserts
the exact mapping: the new `renamed-ligand_haddock.pdb` is the same file on
disk as the old `1LMQ_l_u_haddock.pdb`). The `rigidbody` jobs that read those
topologies then miss.

### 6. `.psf` files are not bitwise reproducible

Axis 0, the precondition study, found that PSFs embed a timestamp:

```
DATE:31-Aug-2026  01:17:08       created by user: unknown
```

PDB outputs are stable across repeated runs and across core counts; PSFs are
not. **Until this is normalised or excluded, a checksum over a PSF cannot
verify anything**, and the five Axis 8 read-set failures below are not
interpretable, because that mode compares a cached result against a freshly
computed one and any PSF will differ.

That is the correct reading of Axis 8's current output: **not** five
undeclared dependencies, but one unresolved determinism question standing in
front of them. Axis 0 is a precondition for a reason.

### 7. Cache resolution costs ~28 ms per job, against a declared 10 ms

Measured as a marginal cost: two all-hit reruns of the same workflow differing
only in job count (`tiny` and `tiny-wide`, 8 and 44 jobs), so startup and the
uncacheable remainder cancel. A hundred-job all-hit run would spend about
2.8 s deciding it had nothing to do.

### 8. Not a caching defect: `postprocess = true` hangs

`haddock3-analyse` raises `numpy.linalg.LinAlgError` inside a worker
(`libalign.kabsch`, on a step with no models to align) and the parent then
blocks forever on the pipe. Reproduces with no `--cache` at all. Two Axis 3
cases run into it; they are marked `known_unrelated` so the failure is not
read as a caching finding.

---

## What Phase 0 says about the instrument

`HADDOCK_CACHE_HARDLINK` is not implemented, so seven of fourteen instrument
checks fail:

| | |
|---|---|
| `=0` must force a copy | still hardlinks |
| `=1` across a filesystem boundary must fail loudly | silently copies |
| an invalid value must be rejected | silently ignored (5 cases) |

The middle one matters most: a silent fallback to copy under `=1` would report
**every** MUST-HIT in the suite as a false miss, which is exactly the trap that
caught my own hand experiments. Phase 2 therefore ran with `--ignore-phase0`
and prints a banner saying its verdicts are provisional.

P0.8 was answered in the affirmative: a `.pdb.gz` source *is* hardlinked as
such, so the compressed-source cases stay in the strong regime where inode
identity is a total observable.

---

## What passed

Worth stating, because it is the load-bearing half:

- **Axis 1 location invariance**, apart from the install path and the input
  rename above: a different `run_dir`, a relocated source, a deeper path, a
  different working directory, a compressed source — all reuse everything.
- **Axis 2 step position**, in full: inserting and removing modules, reordering
  independent ones, repeating a module, and crossing the zero-fill boundary
  where every folder is renamed at once.
- **Axis 4 prefix stability** for `sampling`: raising it from 4 to 6 reuses the
  first four and computes only the two new ones.
- **Axis 12 artifact resolution**, in full: deleted, modified, truncated,
  same-size-replaced, replaced by a directory, by a symlink to a *different*
  result, by a dangling symlink, made unreadable, compressed, or absent
  entirely — each degrades to recomputing exactly that one job and reuses the
  rest. Two stores where one is poisoned resolve from the healthy one.
- **Axis 11 topology**: disjoint caches unioning, overlapping caches agreeing,
  malformed records rejected rather than guessed at, an agreeing duplicate
  accepted silently.
- No **catastrophic** failure was observed anywhere: nothing was served from
  the wrong cache entry, and nothing was served that had to be recomputed,
  except the one classification disagreement below.

The single "served when it must miss" result is `axis6.12`: adding a `REMARK`
line to an input PDB still hits. That is a **classification decision to make
deliberately**, not obviously a defect — HADDOCK3's preprocessing may strip the
line before CNS sees it, in which case the computation genuinely did not
change. The suite declares it MUST-MISS on the argument that a cache should
not decide for itself which byte differences do not matter. Worth settling
explicitly.

---

## What the suite could not reach

24 cases are recorded as scope boundaries rather than run, each with its
reason. Almost all of them need to see the *key* — the internal description of
a job — which the `haddock3` CLI does not expose. They are listed with
`grep -A3 'skip:' end-to-end_tests/caching/cases/*.yaml`.

Three more are unvalidated for want of a fixture: the `topocg`/`cgtoaa` job
shapes, because finding 3 prevents the base run from being built at all.
