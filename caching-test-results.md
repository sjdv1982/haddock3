# CNS caching: first run of the contract test set

Results of running `end-to-end_tests/caching/` against the caching
implementation on `test-system-and-caching-pilot`, on 2026-08-31.

This is a **snapshot of an implementation that is being rewritten**, not a
verdict on the design. The taxonomy in `caching-use-cases.md` is the
specification; the suite is the instrument; this is one reading.

```
Phase 0 (instrument)      7 passed   7 failed
Phase 2 (the test set)   84 passed  56 failed  24 skipped (recorded scope boundaries)
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

### 6. `.psf` files are not reproducible, and it costs far more than it looks

Two runs of the *same* topology, same inputs, same parameters, produce
byte-identical PDBs and **different PSFs**. The whole difference is one line:

```
-  DATE:30-Aug-2026  23:23:49       created by user: unknown
+  DATE:30-Aug-2026  23:24:47       created by user: unknown
```

This is a **HADDOCK3 reproducibility bug, not a caching one**, and it should be
fixed before or alongside the cherry-pick — because until it is, the cache
cannot deliver its main promise:

- Sampling reads the PSF. So a topology computed on Tuesday has a different
  key from the same topology computed on Monday, and **everything downstream
  of topology is unreusable between runs**. Two caches cannot be combined
  unless they happen to share one topology run (`axis11.2`,
  `composed.11x12`).
- It makes the read-set mode uninterpretable. All six of the Axis 8 and
  Axis 10.6 "unsound hit" results report *exactly* the two `.psf` files and
  nothing else — they are this one line, not six undeclared dependencies.
  Axis 0 is a precondition for a reason, and this is what it is protecting.
- No PSF can be gated by checksum at all until the field is normalised away
  or excluded.

**The suite is deliberately not arranged around this.** `axis11.2` combines
two independently produced caches, which is what combining caches actually
means; it could be made to pass by deriving both sources from a single run,
and that would test less while hiding the defect. It fails, and says why.

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
- **Axis 6's semantically-null edits**, on the precise boundary: adding a
  `REMARK` to an input PDB hits, while a whitespace edit to a restraint table
  and a consistent segid rename both miss.
- No **catastrophic** failure was observed anywhere: nothing was served from
  the wrong cache entry, and nothing was served that had to be recomputed.

### A correction, recorded because the reasoning is the useful part

The first draft of this document reported `axis6.12` — adding a `REMARK` line
to an input PDB — as a hit where the suite declared a miss, and called it "a
classification decision to make deliberately".

That was wrong, and so was the case. HADDOCK3 strips `REMARK` records from an
input molecule on the way into the run: the file CNS opens,
`run_dir/data/<step>/<molecule>.pdb`, contains none of them. (Not the
`preprocess` switch, which defaults to false and was off throughout.) The
declared read-set is byte-identical, so the key cannot differ and the hit is
automatic. Axis 6 is about content *that CNS reads*, and this content does not
reach CNS.

The taxonomy does not say otherwise. It groups 6.12–6.15 as semantically-null
edits and leaves them **deliberately unclassified**, noting only that
MUST-MISS and ACCEPTED-MISS collide there. Neither applies: the general rule
settles it without a special case, exactly as it settles 6.7. The suite's
justification — that a cache should not decide which byte differences matter —
was backwards, because the cache never sees the difference.

Worth keeping next to 6.13, which looks like the same kind of edit and has the
opposite verdict: the restraint table *is* copied byte-for-byte, so whitespace
in it does reach CNS and must miss. What separates them is not how meaningful
the edit looks but whether it survives into the read-set — which is the
taxonomy's point about deriving the miss set from content rather than from
judgement.

---

## What the suite could not reach

24 cases are recorded as scope boundaries rather than run, each with its
reason. Almost all of them need to see the *key* — the internal description of
a job — which the `haddock3` CLI does not expose. They are listed with
`grep -A3 'skip:' end-to-end_tests/caching/cases/*.yaml`.

Three more are unvalidated for want of a fixture: the `topocg`/`cgtoaa` job
shapes, because finding 3 prevents the base run from being built at all.
