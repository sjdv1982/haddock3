# CNS caching: test suite plan

## Standing of this document

Companion to `caching-use-cases.md`. That document is the **taxonomy** — what
must hit, what must miss, and why. This document is the **suite** — how those
verdicts are actually observed, given a hard constraint that the taxonomy was
written without:

> **The test suite consists of nothing but ordinary invocations of
> `haddock3 file.cfg`.** No mocks, no dummies, no monkeypatching, no
> test-only entry points, no introspection of implementation internals.

Everything here follows from that constraint plus the caching API. Where this
document and the taxonomy disagree about *method*, this one is later. Where they
disagree about *verdicts*, the taxonomy governs — it is the specification, this
is the instrument.

The implementation is being rewritten (or cherry-picked from the current
branch). This suite is written against the API and the taxonomy, not against any
code, and must survive the rewrite.

**What kind of suite this is.** A **contract compliance test, run
infrequently** — not CI over fragile code. It is run deliberately, by someone
checking that an implementation satisfies the taxonomy, who can read an odd
result and think about it. That governs how much machinery is justified
anywhere in this document: constants over calibration, documented assumptions
over autodetection, a warning over a gate. Where a human running the suite on
purpose can absorb a wrinkle, absorbing it is cheaper than automating it away —
and the automation would itself become something to maintain.

---

## The API under test

- `haddock3 B.cfg --cache OLD-RUN-DIR` — `OLD-RUN-DIR` is a completed (or
  partially completed) HADDOCK3 run directory, used as a source of
  already-computed CNS outputs. On a hit, the cached output is **hardlinked or
  copied** into the new run directory instead of being recomputed.
- **`--cache` is repeatable**: several `OLD-RUN-DIR`s may be supplied, and
  coverage is their union.
- A CNS job primarily produces **a single `.pdb` file**. That file is the unit
  of observation. `topoaa` additionally produces a `.psf`, and is in scope.
- Caching is **opt-in per run**, via `--cache`. There is no "on by default".
- There is no cache-population step: **every ordinary run is automatically a
  usable cache source.** This is what makes a mock-free suite possible at all —
  the fixtures are just runs.
- Reuse is by **content**, not by run identity, workflow identity, filename,
  step ordinal, rank, or HADDOCK3 version. This is the property the suite
  exists to hold down.

---

## The measurement model

The taxonomy required the implementation to emit a per-job executed-vs-reused
signal, on the grounds that a MUST-HIT cannot be asserted by comparing results
(identical results are also what recomputation produces). That requirement is
**withdrawn**. Two black-box observables replace it, and together they are
stronger than the signal they replace.

### Gate 1 — inode identity (primary, per-job, discriminating)

If the cached file is **hardlinked**, then for each output file in the new run
directory:

- **hit** ⟺ `(st_dev, st_ino)` equals that of a file in some `OLD-RUN-DIR`
- **miss** ⟺ it does not

This is not a proxy for the executed-vs-reused signal — it is **strictly more
informative**, because it identifies *which* source entry was used. That is
exactly the assertion the hardest axis needs: on Axis 5, `3_flexref/flexref_1.pdb`
in B must be a hardlink to `2_flexref/flexref_4.pdb` in A — different step
ordinal, different rank, different filename, same inode. A name-keyed or
position-keyed cache fails that assertion loudly, and a key collision (a hit
from the *wrong* entry — the catastrophic failure) is caught by the same check.

Every test case therefore specifies a **mapping**, not a verdict word:

```
B output path  →  expected source path,  or  null (must miss)
```

For `topoaa`, the mapping declares **both** the `.pdb` and the `.psf`, and a hit
must deliver both from the same source job. Partial delivery — `.pdb` linked,
`.psf` recomputed — is a defect, and it is only visible because both are
declared.

**Load-bearing invariant:** the implementation **never content-dedupes outputs
within a run**. Two jobs producing identical bytes must not end up sharing an
inode. If that ever changes, Gate 1 silently stops being a clean hit signal and
this entire suite weakens without failing. It belongs in the implementation's
contract, not just here.

Mechanics: compare `(st_dev, st_ino)`, both fields. `st_nlink > 1` proves *a*
link exists but not *to what*, so it is corroborating evidence only. Do **not**
store inode numbers in the fixture manifest — `stat()` both files at assertion
time. Manifests record *paths*; inode numbers go stale the moment a fixture is
regenerated.

### Gate 2 — wall-clock duration (secondary, aggregate, coarse)

A cache miss runs CNS, which takes seconds to minutes. A test whose jobs must
all hit should therefore finish in roughly the time of a run with no CNS in it.

The bound is built from **declared budgets**, not from measured per-job costs —
declared ceilings are reproducible across machines and need no margin tuning:

```
timeout = overhead(config)
        + Σ over expected misses of  t_miss(module)
        + n_hits × t_hit
```

| Budget | Default | Notes |
|---|---|---|
| `t_miss` — `topoaa`, `rigidbody` | **10 s** per job | |
| `t_miss` — `flexref`, `emref`, `mdref` | **180 s** per job | |
| `t_hit` | **10 ms** per job, **averaged** | per-job jitter is fine; the aggregate is gated |
| `overhead(config)` | measured in Phase 1 | the uncacheable remainder: startup, analysis and clustering modules |

**All three are overrideable per test.**

`t_hit` is not just a timeout — it is a genuine **performance requirement** on
the implementation. A 100-job all-hit run must do its entire cache resolution in
under a second. Checksumming has to be fast or amortised, and the all-hit tests
are where that gets held down.

Gate 2 catches exactly one thing Gate 1 cannot: **a "hit" that ran CNS anyway**
and then discarded the result in favour of the cached file. Inode inspection
would pass that; the clock would not. Conversely Gate 1 catches what Gate 2
cannot: *which* entry was reused. They are complementary, not redundant.

**Gate 2's resolution degrades as the expected miss count rises.** With zero
expected misses the budget is tight and any stray miss blows it. With ten
expected misses the budget is ~100 s (or 30 min for `flexref`), and since the
per-job figures are generous ceilings rather than estimates, an eleventh miss
can hide inside the slack. This is a second reason Gate 1 is primary: it is
exact and per-job at any miss count.

Duration must be **recorded on pass as well as fail**, so drift is visible
before it becomes a flake.

### Gate 2 inverted — the timeout floor (MUST-MISS, and it makes them cheap)

In its upper-bound form Gate 2 says nothing about MUST-MISS: slow is the
uncached default, so slowness carries no signal. The *lower* bound does, and it
turns the most expensive class of test into the cheapest.

Run a MUST-MISS case with a deliberately short timeout `T`, and **assert that
the run had to be killed**:

- the job genuinely missed → CNS starts → still running at `T` → killed → **pass**
- the job wrongly hit → run completes in milliseconds → **fail**

Choosing `T` is easy rather than delicate: hits cost ~10 ms and misses cost
seconds to minutes, so the window is three to four orders of magnitude wide.
This is the opposite of the upper-bound gate, whose margins have to be nursed.

**The budget consequence.** A MUST-MISS probe run this way no longer pays
`t_miss` — it pays `T`, a second or two. The ~10-miss ceiling stops binding, and
the `flexref`/`emref` probes that were restricted to 1–2 misses at 180 s each
become affordable in bulk.

### Which MUST-MISS gate to use

**Inode inspection is the default; the timeout floor is the escape hatch for
expensive modules.** Running a MUST-MISS case to completion and asserting the
output is not a link into any source is simpler in every respect that matters:
no timeout to choose, no process to kill, no process-group cleanup, no vacuous
pass to guard against, and it proves recomputation *succeeded* rather than
merely that a hit did not happen. Its only cost is CNS time.

So the rule is a cost threshold, not a preference:

| Module | MUST-MISS method | Why |
|---|---|---|
| `topoaa`, `rigidbody` (10 s) | **run to completion, inode** | 10 s is not worth the machinery |
| `flexref`, `emref`, `mdref` (180 s) | **timeout floor**, plus at least one run-to-completion case per shape | 180 s × many probes is not affordable; the completion case covers what the floor cannot prove |

Written the other way round: reach for the timeout floor only when `t_miss` is
what is actually stopping you from writing the probe.

Three limits of the timeout floor, one of them a genuine hazard:

- **It proves "did not hit", not "recomputed correctly".** The run is killed
  before any result exists. That is the right target — the wrong hit is the
  catastrophic failure — but a subset of cases, at least one per module shape,
  must still run to completion. Otherwise a perturbation that makes CNS *fail*
  is indistinguishable from one that makes it recompute.
- **A crash also fails the test**, since it exits early. That fails safe, but a
  legitimately-crashing perturbation and a wrong hit look alike from outside and
  need investigation to separate. Assert explicitly that the process was alive
  at `T` and had to be killed — never accept a self-exit as a timeout.
- **The vacuous pass is the real hazard.** If `T` is shorter than the time to
  *reach* the perturbed job, the run times out before caching was ever
  consulted, and the test passes having proved nothing.

**The implementation must not be assumed to resolve hits up front.** Resolution
may be interleaved with execution, so at kill time a job that would have hit may
simply not have been reached yet. Everything below is written to hold under
interleaving; anything that depends on up-front resolution is unsound here.

**Guarding against the vacuous pass.** Two guards, both valid under
interleaving; use the first always and the second where it is available.

1. **All-hit calibration (primary).** Run the *unperturbed* config against its
   own cache and measure `t_allhit` — every job hits, so this costs milliseconds
   per job. If `t_allhit ≪ T`, then the perturbed run still being alive at `T`
   means it did something the unperturbed run did not, which is CNS execution.
   This makes no assumption about ordering or resolution strategy at all. It is
   valid precisely where timeout-floor mode is used: MUST-MISS probes on Axes
   0b, 6, 7 perturb content or parameters, not workflow shape, so A and B have
   the same schedule to compare. It is *not* valid where the perturbation
   changes the workflow's length (Axes 2, 4) — those cases must run to
   completion instead.
2. **Earlier-step witness (reinforcement).** HADDOCK3 executes steps
   sequentially: step *N+1* does not begin until step *N* finishes. So a
   MUST-HIT job in a **strictly earlier step** than the perturbed job must be
   present and linked at kill time — reaching the perturbed job implies the
   earlier step completed. Within a single step, job order is not guaranteed
   under the parallel scheduler, so the witness must be in a previous step,
   never a sibling.

**Partial-directory inspection.** Gate 1 is not wholly lost on a killed run, but
**in this mode, and only in this mode**, it is one-sided. Assertable: a
declared-miss output that is **present and linked into a source is a failure** —
the catastrophic case is still caught. Not assertable: the absence of a declared
hit, which under interleaving is indistinguishable from not-yet-reached. Only
the earlier-step witnesses above carry positive information.

This one-sidedness is an artifact of killing the run, not a property of the
cache. In `mode: complete` the run finishes, every job is reached, and the
mapping is asserted **two-sided and exactly**: a declared hit that is missing or
unlinked is a failure (a false miss — Axis 1–5 territory, costing time), and a
declared miss that is linked is a failure (the catastrophic one). Nothing about
interleaving weakens that.

**Harness requirement:** kill the whole **process group**, not just the
`haddock3` parent. One orphaned CNS process per MUST-MISS test will accumulate
fast in a suite that now deliberately kills hundreds of runs.

### Gate 3 — content equality (weak, fallback only)

Byte-identity between B's output and the source is **not** evidence of a hit: a
deterministic recomputation produces the same bytes. Content is used only to
confirm that a *copy* (rather than a link) delivered the right bytes, and to
assert MUST-DEGRADE fell back correctly.

*Considered and rejected:* marking cache entries — appending a benign `REMARK`
to a source `.pdb` so that a hit is distinguishable by content in copy mode.
It conflicts with checksum verification of artifacts on read (taxonomy Axis 12),
under which a tampered artifact must degrade, not hit. The trick would only work
against an implementation that fails Axis 12.

### The regime this forces

**The suite runs with `HADDOCK_CACHE_HARDLINK=1` by default**, because that is
the only regime in which Gate 1 is a total observable. In copy mode Gate 1 is
blind and the *only* hit evidence is the clock — so copy-mode cases have
**structurally degraded assertion power**, and must be labelled as such rather
than quietly trusted. Which cases those are is determined by the next section.

---

## `HADDOCK_CACHE_HARDLINK`

An **environment variable** — deliberately not a config parameter, so that it is
structurally outside the config file and cannot enter the cache key even by
accident. A config parameter would have to be *tested* for key-invariance
(Axis 3); an env var makes that untestable-because-impossible. This is the
"discharge by construction" pattern the taxonomy recommends, applied to the test
instrument itself.

| Value | Meaning |
|---|---|
| undefined | best-effort: hardlink where possible, copy otherwise |
| `0` | force copy, always |
| `1` | force hardlink; **any** hardlinking failure, for any reason, is an error |

The `1` case matters more than it looks: **a silent fallback to copy under `=1`
would report every MUST-HIT in the entire suite as a false MISS.** Failing
loudly is what keeps the instrument honest.

### Consequence: the compressed-source cases leave the strong regime

Because *any* hardlink failure under `=1` is an error, any case whose source
cannot be linked must run under `=0` or undefined — where Gate 1 is blind and
only Gate 2 and Gate 3 remain. Cross-filesystem sources (Axis 1.8, 12.12) are
certainly in this class.

Compressed sources (Axis 1.7 — cache cleaned/compressed after A finished;
Axis 12.7 — artifact recompressed, MUST-HIT) depend on an implementation
capability: if a `.pdb.gz` source can be hardlinked *as such* into the new run,
these stay in the strong regime and Gate 1 works on the `.gz`; if the
implementation must decompress, `=1` errors and they move to copy mode. The
branch already carries "support hardlinking of zipped PDBs", so the first
reading is likely — but the suite should assert which it is (P0.8) rather than
assume.

---

## Three phases

**The phases are a strict sequence, not a grouping.** The test suite proper
(Phase 2) is not a standalone artifact: it consumes `OLD-RUN-DIR`s that do not
exist until the Phase 1 generator scripts have been run, and its assertions are
meaningless until the Phase 0 instrument checks have passed. A checkout of the
repository cannot run Phase 2. Phase 1's output is a **prerequisite build
artifact**, in the same way a compiled binary is — regenerable, not committed,
and required before anything downstream will work.

Ordering: **Phase 0 → Phase 1 → Phase 2**, with Phase 0 aborting the run on
failure rather than skipping, and Phase 2 failing fast with a clear "corpus not
built" message rather than an inscrutable missing-path error.

### Phase 0 — pre-test-suite: validate the instrument

Runs first. If it fails, **the rest of the suite is meaningless and must not
run** — abort, do not skip-and-continue. It tests the measuring device, not the
feature.

| # | Setup | Assertion |
|---|---|---|
| P0.1 | same fs, `=1`, full cache | every reused file shares `(dev,ino)` with its source |
| P0.2 | same fs, `=0`, full cache | inodes differ, content identical, run succeeds |
| P0.3 | **cross fs, `=1`** | run **fails loudly**, defined error, non-zero exit — never a silent copy |
| P0.4 | cross fs, `=0` | copies, run succeeds |
| P0.5 | cross fs, undefined | best-effort → copies, run succeeds |
| P0.6 | same fs, undefined | best-effort → hardlinks |
| P0.7 | invalid value (`2`, `yes`, empty) | defined error, not a silent fall-through to default |
| P0.8 | same fs, `=1`, source `.pdb` compressed | **either** links the `.gz` (Gate 1 stays live) **or** errors — pins down which, and thus which regime Axes 1.7 / 12.7 run in |
| P0.9 | same fs, `=1`, source read-only / other owner | hardlink succeeds (link needs target-dir write, not source write) |
| P0.10 | same fs, `=1`, multiple `--cache` sources | links resolve from whichever source holds the entry |

P0.3–P0.5 need **two filesystems**. Without root, `/dev/shm` is usually a
separate tmpfs and works; otherwise a loopback mount. Provide a fixture that
locates one and skips with an explicit message if it cannot — and record that
skipping P0.3 leaves the main suite's cross-filesystem cases **unvalidated**,
which is a coverage hole, not a neutral skip.

### Phase 1 — generators: build the `OLD-RUN-DIR` corpus

**Scripts, not tests, and they must be run before the suite exists in any
usable form.** Expensive (real CNS), run once, reused by the whole suite. This
is where the CNS wall-time budget is spent — the taxonomy's conclusion that CNS
time, not test-writing effort, is the scarce resource applies directly.

What the scripts actually do is deliberately ordinary: **invoke `haddock3` on a
config, then edit the resulting directory's contents.** Nothing else. There is
no cache-population mode to write, because every run is already a usable cache
source; and the damaged and interrupted fixtures are produced by acting on real
run directories from the outside, which is what keeps the whole approach
mock-free.

Three kinds of fixture, in increasing cost and decreasing reliability:

1. **Base runs** — ordinary completed `haddock3` runs. A *small* set, each
   serving many test cases. Minimising this set is the main cost lever.
2. **Derived-by-damage** — a base run copied (content copy, fresh inodes) and
   then damaged: output deleted, truncated, modified in place, replaced by a
   same-size file, replaced by a directory or symlink, `chmod 000`, compressed,
   relocated to another filesystem. Cheap: no CNS. Covers most of Axis 12 and
   part of Axis 9.
3. **Interrupted runs** — `haddock3` started and killed (SIGINT vs SIGKILL) at
   chosen points. Genuinely requires running and killing a real process; this is
   the least deterministic generator and the likeliest source of flakes. Axis 9.

Each base run writes a **manifest** recording: the config used, the run
directory path, every cached output path with its producing module, and the
measured **`overhead(config)`** — the uncacheable remainder (startup, analysis
and clustering modules) that Gate 2's bound is built on. This is the only
measured quantity in the timing model; the `t_miss` and `t_hit` figures are
declared.

**The corpus needs at least two sizes**, and this follows directly from the
~10-miss budget:

- a **tiny** base run, for perturbations that invalidate *everything* — a toppar
  file changed (Axis 6.8), `iniseed` changed (Axis 7.3), the canonicalization
  input set changed. On a large run these produce hundreds of misses and are
  unaffordable; on a tiny run they are a handful.
- a **larger** run reaching a selection/clustering module with enough models for
  clustering to be meaningful — required by Axis 5. Compatible with the budget
  because Axis 5's cases are overwhelmingly MUST-HIT.

For the expensive modules the ~10-miss ceiling is nominal: at 180 s per job, ten
`flexref` misses is a half-hour test. Practical budgets there are **1–2 misses**
per run-to-completion case. MUST-MISS probes escape this via the timeout floor;
**MUST-HIT and partial-hit cases do not**, since they must finish to be
asserted. So it is the mixed cases on expensive modules — Axis 4's prefix
boundaries, Axis 6's read/not-read partitions — that stay narrowly localised by
construction, not the pure MUST-MISS probes.

**Corpus hygiene:** `OLD-RUN-DIR`s are **read-only** during the suite — `chmod`
them, and never point a run's `run_dir` into one. A test that writes into a
fixture invalidates every inode assertion that follows it.

**System sizing:** base runs must be small enough that a `topoaa`/`rigidbody`
job lands well under its 10 s ceiling. Choosing the smallest viable molecular
system is a prerequisite, not a detail.

The corpus is a build artifact: regenerable, not committed.

### Phase 2 — the suite proper

Each test case:

```yaml
case: axis5.3-rank-changed
mode: complete                           # complete | timeout-floor
sources:                                 # --cache is repeatable
  - base/pp-cluster-1
config: configs/axis5_3_B.cfg
env:  {HADDOCK_CACHE_HARDLINK: "1"}
expect:
  "3_flexref/flexref_1.pdb": "base/pp-cluster-1/2_flexref/flexref_4.pdb"
  "3_flexref/flexref_2.pdb": null        # must miss
misses: {flexref: 1}                     # drives the Gate 2 bound
overrides: {}                            # per-test t_miss / t_hit / overhead
```

```yaml
case: axis7.3-iniseed-changed-flexref
mode: timeout-floor
sources: [base/pp-small-1]
config: configs/axis7_3_B.cfg
env:  {HADDOCK_CACHE_HARDLINK: "1"}
floor: 5s                                # must still be running at T
calibrate: base/pp-small-1               # all-hit run of the unperturbed config; t_allhit << floor
witness: "1_topoaa/*.pdb"                # earlier step than the perturbed job: must be linked at kill time
expect:
  "3_flexref/flexref_1.pdb": null        # if present AND linked -> catastrophic failure
```

Harness, `mode: complete` — run `haddock3 <config> --cache <s1> --cache <s2> …`
as a subprocess with the given env, measure wall time, then `stat()` each
declared pair. Assert the mapping exactly (every declared hit links to its
declared source, every declared miss links to nothing in any source) and assert
duration within the derived bound.

Harness, `mode: timeout-floor` — same invocation, but assert the process was
**still alive at `floor`** and had to be killed; a self-exit before `floor` is a
failure whatever its exit code. Kill the process group. Then, in the partial
directory: the `witness` (an earlier step) must be present and linked, and each
declared miss must be either absent or present-and-unlinked. The `calibrate`
run supplies `t_allhit` and is the primary anti-vacuity guard.

Because hits are fast, **Phase 2 is fast**. The suite demonstrates the feature by
being cheap to run. Split it from Phase 1 with pytest markers so the fast tier
is the default.

Location: `end-to-end_tests/caching/` — these are real CNS-executing invocations
of the CLI, which is what that tier is for.

---

## Machine requirements and threshold profiles

**This suite is a performance test as well as a correctness test, and therefore
cannot be run on arbitrary hardware with the default thresholds.** A 10 ms
average per cache hit presumes an **SSD** and a machine that is not heavily
loaded; on a spinning disk, a contended network filesystem, or a saturated CI
runner, that budget is missed by an implementation that is working perfectly.
Left unaddressed, the suite would report performance-driven false failures as
if they were caching defects — which is worse than not running it.

Two things follow.

### The correctness half is machine-independent; separate it

Not all gates are equally exposed:

| Gate | Machine-dependent? |
|---|---|
| Gate 1 — inode identity | **no.** Pure filesystem identity. Valid on any hardware, at any speed. |
| Gate 3 — content equality | **no.** |
| Gate 2 upper bound, `t_hit` (10 ms/job) | **yes, strongly.** Disk-bound: checksumming inputs means reading them. |
| Gate 2 upper bound, `t_miss` (10 s / 180 s) | **yes.** CNS compute speed, CPU-bound. |
| `overhead(config)` | measured per machine already. |
| Gate 2 inverted — the timeout floor | **weakly.** It needs `t_allhit ≪ T ≪ t_miss`; a slow machine narrows that window but it stays orders of magnitude wide. The most portable of the timing gates. |

So the **entire catastrophic-failure surface — every MUST-MISS assertion — rests
on machine-independent gates**, plus the most portable of the timing ones. Two
consequences, both cheap: a marker separating the timing assertions from the
rest, so the correctness suite can be run in full on a laptop; and, when reading
results from unqualified hardware, the knowledge that **an inode failure is real
wherever it appears**, while a timing failure there may be nothing.

### Thresholds are documented assumptions, not measurements

The figures in the measurement model (10 ms, 10 s, 180 s) are **defaults for a
reasonable machine with an SSD**, stated as constants in one place and
overridable globally and per test. That is the entire mechanism: no
autocalibration, no machine profiles, no preflight.

This follows from what the suite is for. Run infrequently and on purpose, it
does not need to defend itself against unknown hardware — the person running it
can recognise a timing failure on a slow disk and change a constant. Machinery
to spare them that would cost more than it saves.

The documented assumption is therefore the deliverable here, not a mechanism:
**SSD, unloaded machine**. Anyone running elsewhere adjusts the constants and
knows they have done so.

One thing to keep in view when adjusting them: `t_hit` is not an arbitrary
margin but a **genuine performance requirement** on the implementation — a
100-job all-hit run resolving in under a second. Relaxing it 50× to accommodate
slow storage does not loosen a test, it deletes a requirement.

---

## What this instrument can and cannot reach

The mock-free, `haddock3`-only constraint moves several taxonomy items out of
scope. They are **not thereby dropped** — see "The companion suite" below.

| Axis | In this suite? | Notes |
|---|---|---|
| 0 — CNS determinism | **precondition study** | N uncached runs of one config, compare bytes. Not a cache test; it bounds what the rest may assert. Run before Phase 2 is trusted. |
| 0b — binding | **behavioural half only** | 0b.1 (molecule swap), 0b.4 (role change), 0b.5 (output shape — now observable, since `topoaa`'s `.psf` is declared) are cfg pairs → in. 0b.7–0b.10 (rule drift, pin-table stability, unclassifiable-reference hard error, canonical-form leaks) are white-box → **out**; their symptoms surface as Axes 1 and 2. |
| 1 — location/naming | **fully in** | Pure cfg + filesystem moves. Cheapest high-value material. 1.7 (compressed source) and 1.8 (cross-fs) may fall into the degraded copy-mode regime — see P0.8. |
| 2 — step position | **fully in** | cfg edits, incl. the zero-fill boundary (2.5). |
| 3 — orchestration params | **in, with a carve-out** | `clean`, `postprocess`, `gen_archive` rewrite or compress outputs and thereby **destroy Gate 1**. Main suite runs with all three off; they get dedicated cases asserted on Gate 2 + decompressed content. |
| 4 — job counts | **fully in** | cfg edits; prefix-stability is directly visible in the mapping. |
| 5 — selection/order | **fully in — headline** | The axis inode inspection was made for. Needs the larger base run. |
| 6 — input content | **fully in**, except 6.10 | 6.10 (CNS executable changed) needs two CNS binaries — likely impractical; flag rather than fake. Global-invalidation cases (6.8, toppar) go on the tiny base run. |
| 7 — scientific params | **fully in** | cfg edits. 7.3 (`iniseed`) is global → tiny base run. |
| 8 — read-set completeness | **weak half only** | Env var, locale, timezone, CWD, symlink-target perturbations are user-level → in. The **strong form** (execute a job with only its declared dependencies present) → companion suite. |
| 9 — interruption | **in, flakiest** | Real SIGINT/SIGKILL plus post-hoc damage. Needs deliberate fault injection; will not be reached by luck. |
| 10 — conflict discrimination | **partly in** | Two sources disagreeing on one job is directly constructible now that `--cache` is repeatable, and *which won* is observable by inode. Whether the system **classified** the conflict as nondeterminism-vs-under-specification is not user-observable → companion suite. |
| 11 — cache topology | **fully in**, minus 11.9 | Repeatable `--cache` makes disjoint / overlapping / unrelated-workflow / superset / zero-overlap all directly expressible. **11.9 (records without bytes) is explicitly out of scope for this suite** — the state exists, but it is not this instrument's concern. |
| 12 — artifact resolution | **fully in** | Damage-derived fixtures; cheap, no CNS, high value. All outcomes are MUST-DEGRADE, which Gate 1 reads as "no link" and Gate 3 confirms. |
| 13 — job shapes | **in, budget-limited** | Multiplier over the above. `topoaa` in scope with both outputs declared; `flexref`/`emref` coverage constrained by the 180 s/job budget. |
| Composition | **in, last** | Only after the single-axis matrix is green. |

**Ordering.** Keep the taxonomy's principle: **MUST-MISS probes before MUST-HIT
cases.** A key that is too invariant passes every MUST-HIT test perfectly, and
those are the failures that cost correctness rather than time. Concretely:
Phase 0 → Axis 0 study → MUST-MISS probes across 0b/6/7/8 on the tiny base run →
Axis 12 (cheap, no CNS) → Axes 1–4 → Axis 5 → Axes 9–11 → Axis 13 → composed.

---

## The companion suite (out of scope here, noted for completeness)

The gaps above — Axis 0b's structural half, Axis 8's strong form, Axis 10's
conflict classification — are all the same kind of gap: they require seeing a
job's **key and declared dependencies**, which the `haddock3` CLI does not
expose and which no black-box observable can reconstruct.

The intended resolution is to **dump each job as a `seamless-run` command** and
test at that level, in a separate suite. This is out of scope for the present
test set, but it is worth recording *why* it closes exactly these gaps and not
others:

- A dumped command is self-contained by construction: it carries precisely the
  declared dependencies and nothing else. **Running it *is* Axis 8's strong
  form** — the isolated-execution harness the taxonomy calls its highest-value
  test. If the job runs there, the read-set is complete; if it fails, an
  undeclared dependency has been converted from a silent key defect into a loud
  one.
- The dump makes the key and the pin table **directly inspectable**, which is
  what Axis 0b's structural cases (0b.7–0b.10) and the golden-canonical-form
  check need, with no CNS execution at all.
- Two dumps for the same job with different results make an Axis 10 conflict
  **classifiable** rather than merely observable.

So the split is clean: **this suite tests the cache as a user experiences it;
the companion suite tests the key as the implementation constructs it.** Neither
subsumes the other, and the black-box suite should not be contorted to reach
into the second half.

---

## Open questions

Substantially cleared. What remains:

1. **CNS wall-time budget for Phase 1** — sets the number and size of base runs,
   hence the breadth of Axis 5 and Axis 13.
2. **Choice of molecular system(s)** for the tiny and larger base runs. Sets
   every Gate 2 margin in the suite.
3. **P0.8's answer** — whether a compressed source can be hardlinked as such.
   Not blocking (the test determines it), but it decides whether Axes 1.7 and
   12.7 run in the strong or the degraded regime.
4. **Axis 6.10** (CNS executable changed) — is a second CNS binary available in
   the test environment, or is the case declared untestable?

Settled, and recorded here because the suite depends on it: **cache resolution
may be interleaved with execution** and must not be assumed to happen up front.
This is what makes the all-hit calibration, rather than partial-directory
inspection, the primary anti-vacuity guard in timeout-floor mode. It affects
nothing in `mode: complete`, where every job is reached and the mapping is
asserted exactly.

A separate consequence for **Axis 9**, which is easy to conflate with the above
and is not the same thing: there, the *source* run is the interrupted one and B
runs to completion, so B's assertions are ordinary two-sided complete-mode
assertions. What is uncertain in Axis 9 is the **fixture**, since the kill lands
where it lands. So the expected mapping for those cases must be **derived from
what the interrupted fixture actually contains**, inspected after generation,
rather than declared a priori.
