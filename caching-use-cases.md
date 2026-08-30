# CNS caching: perturbation taxonomy for test-set design

## Standing of this document

This document is **an input to a fresh implementation**, and a taxonomy for the
test set that implementation must satisfy. The verdicts below (MUST-HIT,
MUST-MISS, MUST-DEGRADE) are therefore **requirements on the key**, not merely
expectations about existing behaviour.

Context for a reader arriving cold:

- The branch's original focus was a witness-based testing system for CNS
  (`CNS_WITNESS_SEAMLESS_PILOT_PLAN.md`). That focus is **postponed**. The
  means-to-an-end — **CNS-level caching** — is what has been accepted, on its
  merits for users.
- A caching implementation exists on this branch. **It is being rewritten. Do
  not port its code.** What is worth reusing is the *reasoning* in the design
  documents:
  - `caching-redesign-plan.md` — the canonicalization problem, the reference
    set of job-varying inputs, the Seamless checksum mechanics, and the
    dependency-scan requirements. Substantially sound.
  - `caching-redesign-handoff-plan.md` — implementation-level decisions;
    useful as a record of what was considered, not as a specification.
  - `CNS_WITNESS_SEAMLESS_PILOT_PLAN.md` — the three-layer
    dependency/artifact/witness model, and Phase 3's artifact-reproducibility
    analysis, which is Axis 0 here.
- **Where those documents and this one disagree, this one is the later
  reasoning.** The specific points on which the previous design should not be
  reproduced are listed in "Errors this taxonomy has already corrected" below,
  and in "Open decisions" at the end.

Because the implementation is being written fresh, most of this taxonomy should
be discharged **structurally rather than empirically** — see "Structural
invariants versus empirical tests". Designing for that is the main leverage
available, and it is why the taxonomy is worth reading before the code is
written rather than after.

A test case is a **pair of runs**, A then B, where B differs from A by exactly
one controlled perturbation, plus an **expected verdict for every CNS job in B**.
The taxonomy enumerates the *kinds* of perturbation, not the reasons a user
might apply them.

## What is actually being cached

The cached unit is a **CNS job**, not a HADDOCK3 step. A CNS job is a closed,
pure function: (canonical script + every file it reads + the executable) →
(PDB, optionally PSF). HADDOCK3 is not a participant in that function — it is
the machinery that *authors* it. HADDOCK3's version, module structure, config
file, and step numbering are all outside the computation.

Two consequences that shape the whole taxonomy:

- **Reuse is not scoped to "the same workflow", or even to HADDOCK3.** Any
  producer emitting a bit-identical **canonical transformation** — same input
  bindings, same content, same script, same output shape — has produced the same
  job. A different workflow, a different module, `haddock3-score`, a
  hand-written CNS job, or a different HADDOCK3 version are all legitimate
  sources.
- **A path is a locator; a checksum is an identity.** Where bytes live is not
  part of what a result *is*. This separates cleanly into two layers, and the
  test set must respect the separation:

  | Layer | Maps | Contains paths? | Meaningful without bytes? |
  |---|---|---|---|
  | Result cache | job checksum → result checksum | no | **yes** |
  | Artifact store | result checksum → bytes | yes | no |

  A design that carries a path inside a result record fuses the two, and every
  question about *where* an artifact lives then masquerades as a question about
  *integrity*. Axes 11 and 12 below are separated on this basis.

- **On the input side, content is necessary but not sufficient.** A
  transformation is `{pin name: content checksum}` plus script plus output
  shape, and *all of it* is hashed. The same bytes bound to a different pin is a
  different computation. So an input file is not simply "excluded from the key
  because its path is a locator" — its path is **erased and replaced by a
  canonical pin name that is itself in the key**. Location invariance (Axis 1),
  position invariance (Axis 2) and rank invariance (Axis 5) are therefore not
  properties of *ignoring* names; they are properties of the **name → pin
  mapping being invariant** under those perturbations. This requires input
  canonicalization to be a well-founded, deterministic function of role and
  content — which makes the canonicalization rule itself a dependency of every
  key in the system. That is Axis 0b.

## How to classify a perturbation not listed here

The enumerated cases are examples of a small number of properties. For any new
candidate perturbation, three questions settle it, in order:

1. **Does it change the content of anything in the declared read-set?**
   → MUST-MISS, localised to the jobs that read the changed thing.
2. **Does it change a *binding*** — which pin an input occupies, or the output
   shape? → MUST-MISS. Same bytes in a different role is a different
   computation.
3. **Does it change only a *locator*** — a path, a filename, a step ordinal, a
   rank, a run directory, a version label, a store location? → MUST-HIT.

Three categories, not two. The two-category version ("content vs path") is
wrong, and wrong in a way that is easy to reach: it makes rank-invariance look
like "names never matter", which then makes a molecule swap look like it should
hit. Binding is the category that keeps locator-erasure from over-shooting.

If the answer is "none of the three" — the perturbation changes something that
influences the result but appears in no read-set, no binding, and no locator —
then the key is **unsound**, and the finding belongs in Axis 8, not in whichever
axis the perturbation superficially resembles.

## Errors this taxonomy has already corrected

Recorded because they are natural to re-derive, and each one changes the shape of
the test set rather than a detail in it.

| Mistake | Correction |
|---|---|
| Treating the cache as a cache of **HADDOCK3 steps** | It caches **CNS jobs**. HADDOCK3 is not a participant in the computation; it is the machinery that *authors* it. Its version, module structure, config and step numbering are outside the function. |
| Filing "source from a different HADDOCK3 version" as an **integrity** concern | It is not a perturbation kind at all. It is a bundle of Axis 6 + Axis 7 content changes, and its verdict is a precise, automatically-derived partition. A version stamp in the key would replace a content-derived answer with a proxy — the same error as keying on filenames. (Axis 6.16) |
| Filing "artifact outside the source directory" as an **integrity** concern | A path is a locator. Verification is by checksum, so a bad locator can only fail to find bytes, never yield wrong ones. Containment is a policy about trust and lifetime, not correctness. (Axis 12) |
| Treating a **conflicting record** as a bookkeeping nuisance (precedence, or hard error) | It is evidence, and the only evidence the system gets free. It means either nondeterminism (artifact at fault) or under-specification (key at fault); these have opposite remedies and must be told apart. (Axis 10) |
| Over-correcting: extending "paths are locators" to the **input** side | A transformation is `{pin: checksum}` + script + output shape, all hashed. An input's path is not ignored — it is *erased and replaced by a pin name that is in the key*. (Axis 0b) |

The through-line: every one of these is the same failure — reasoning about
identity in terms of a **proxy** (a version, a directory, a filename, a rank)
instead of in terms of **content and binding**.

## The test set is written against this document, not against a codebase

A consequence of writing the taxonomy before the implementation: the test set
must be expressed in terms of the **three observables of the model**, never in
terms of an implementation's internal artifacts.

| Observable | Question it answers |
|---|---|
| did CNS execute for this job? | MUST-HIT / MUST-MISS |
| what key did this job receive? | Axes 0b–8, testable without running anything |
| what result is bound to that key? | Axes 0, 9–12 |

Tests phrased in terms of a particular record format, file layout, or CLI flag
are tests of an implementation, and they die with it. Tests phrased in terms of
the three observables above survive a rewrite — which is the point, since this
document is being written precisely so that it outlives the code it will first
be applied to. The implementation must expose all three; that is a requirement
on it, not a convenience for the tests.

## Verdict vocabulary

Every job in B falls into one of five classes. A test case is specified by
partitioning B's jobs across them.

| Verdict | Meaning | What a violation means |
|---|---|---|
| **MUST-HIT** | the key is required to be invariant to this perturbation | a miss is a lost opportunity (wasted compute), diagnosable and non-fatal |
| **MUST-MISS** | the perturbation changes the computation | a hit is **silently wrong results** — the only catastrophic failure |
| **MUST-DEGRADE** | the artifact cannot be obtained; must fall back to execution | a hit is silently wrong; a hard error is over-strict |
| **NO-JOB** | the job does not exist in B | — |
| **ACCEPTED-MISS** | a hit the design deliberately declines to deliver | not a defect — an explicit scope boundary, recorded so it is not later mistaken for one |

The asymmetry is the organising principle of the whole test set: MUST-HIT
failures cost time, MUST-MISS failures cost correctness. The test set should be
weighted accordingly — every axis needs at least one MUST-MISS probe sitting
immediately adjacent to its MUST-HIT cases, because a key that is *too*
invariant passes every MUST-HIT test perfectly.

**Observability requirement.** A MUST-HIT cannot be asserted by comparing
results: identical results are also what a *recomputation* produces. The
assertion must be that **CNS did not execute**. Likewise MUST-MISS asserts that
CNS *did* execute. The harness therefore needs an execute-vs-reuse observable
that is independent of the result — without it, most of this matrix is
untestable, and Axis 0 is why.

---

## Axis 0 — Determinism of the computation itself

Not a caching test. The **precondition** for every MUST-HIT in the matrix being
meaningful, and the thing that makes conflicts (Axis 9) interpretable.

0.1 Run the same job N times in fresh directories; result checksum stable?
0.2 Same, with concurrency varied (serial vs saturated machine).
0.3 Same, across machines with different CPU/library versions.
0.4 Which artifacts are raw-bitwise stable, which become stable only after
    normalisation, and which are stable only at the witness level (score,
    energies, RMSD)?
0.5 Are the volatile fields exhaustively known, or only the ones seen so far?

Only artifacts in the first two classes of 0.4 can be gated by checksum at all.
This axis determines what the rest of the test set is permitted to assert.

---

## Axis 0b — Canonicalization and input binding

The second precondition, paired with Axis 0. Axis 0 asks *is the computation
stable?*; this asks *is the identity function well-founded and stable?* Neither
is a perturbation of a user workflow, and until both hold, no verdict elsewhere
in the matrix means anything.

**Binding is part of identity.** The same bytes used in a different role are a
different computation, and must miss:

0b.1 Two input molecules swapped between pins, same bytes. → MUST-MISS.
0b.2 The same file bound to two pins within one job.
0b.3 Two *different* files with identical content in different roles → distinct
     pins sharing one content checksum.
0b.4 A file's role changes (was `ambig`, now `unambig`), content unchanged.
     → MUST-MISS.
0b.5 Output shape changes (PDB → PDB+PSF) with identical inputs. → MUST-MISS:
     the output binding is part of the transformation, not a detail of what gets
     written afterwards.
0b.6 A reference whose role is genuinely ambiguous (a `.tbl` that could bind to
     more than one role) — the rule must be total and deterministic, not
     first-match-wins by accident.

**The naming rule is a dependency of every key.** This is the failure mode none
of the other axes can see:

0b.7 The canonicalization rule itself changes — e.g. `canonical-input-{i}` is
     assigned by order of first reference and becomes assigned by sorted
     filename. **Every key in the system changes**, though no file content
     changed (invisible to Axis 6), no read-set changed (invisible to Axis 8),
     and no science changed. A pure refactor silently invalidates every cache in
     existence.

     This is the one legitimate version-like dependency in the design, and —
     unlike the HADDOCK3 version, which must *not* be pinned (Axis 6.16) — it
     either has to be stable by construction and tested as such, or explicitly
     versioned in the key. The cheap, high-value test is a **golden canonical
     form**: freeze the canonical script and pin table for one job of each shape
     in the repository, so any change to the rule surfaces as a reviewable diff
     rather than as a mysterious global cache miss months later.

0b.8 Pin assignment perturbed by something incidental — add a comment line to the
     generated `.inp`, or reorder two independent statements, and check the pin
     table does not reshuffle.
0b.9 An unclassifiable reference. If the rule falls back to a bucket that embeds
     an absolute path, the key silently becomes location-dependent and **all of
     Axis 1 fails as false misses**. Must be a hard error, not a fallback.
0b.10 Canonical script completeness: no run-directory path, install path, or
      step-folder token survives into the canonical form. A leak converts Axes 1
      and 2 from MUST-HIT into silent false misses that read as cache
      inefficiency rather than as a defect.

The last two are why this axis is tested *first*: 0b.9 and 0b.10 are the
mechanisms by which a location- or position-dependent key masquerades as a
merely disappointing one.

---

## Axis 1 — Location and naming of the run

Nothing about the computation changes; only where it sits and what it is called.
Whole axis is **MUST-HIT** (100% of jobs).

1.1 Different `run_dir` name.
1.2 Cache source relocated/renamed *after* A finished.
1.3 Different HADDOCK3 installation path.
1.4 Different current working directory at invocation.
1.5 Different absolute path depth (short vs deeply nested).
1.6 Input molecule files moved or renamed, content unchanged.
1.7 Cache source compressed/cleaned after A finished.
1.8 A and B on different filesystems (forces copy rather than link).

Adjacent MUST-MISS probe: input file renamed **and** one atom changed.

---

## Axis 2 — Step position within the workflow

The job is unchanged; its step folder's ordinal is not. Whole axis is
**MUST-HIT**.

2.1 Insert a non-CNS module upstream of a CNS module.
2.2 Insert a CNS module upstream of a CNS module.
2.3 Remove a module.
2.4 Reorder two independent modules.
2.5 **Cross the zero-fill boundary**: workflow grows from 9 to 10+ steps, so
    `1_topoaa` becomes `01_topoaa`. Distinct from 2.1 — it changes the folder
    name of *every* step, including ones not otherwise touched.
2.6 Same module repeated (two `[caprieval]` blocks) — checks that a job's
    identity does not absorb its step's occurrence index.
2.7 The same CNS module at a different position doing identical work.

---

## Axis 3 — Orchestration parameters

Parameters consumed by Python, never by CNS. Whole axis is **MUST-HIT**.

3.1 `ncores`.
3.2 `clean`, `postprocess`, `gen_archive`, `offline`.
3.3 `debug`.
3.4 `tolerance` / `faulty_tolerance`.
3.5 Config cosmetics: comments, whitespace, parameter ordering.
3.6 A parameter written out explicitly at its own default value.
3.7 A parameter set on the module that is only read by a *different* module.

Adjacent MUST-MISS probe: a parameter that looks orchestrational but reaches the
`.inp`. Each such parameter is worth an individual test, since the boundary
between "Python-only" and "reaches CNS" is per-module and is exactly where an
over-invariant key would be introduced by accident.

---

## Axis 4 — Job-count perturbations (prefix stability)

The schedule grows or shrinks; the retained jobs must be untouched. Verdict:
**MUST-HIT on the intersection, NO-JOB / MUST-MISS on the difference.**

4.1 `sampling` increased. Expect: first N MUST-HIT, remainder MUST-MISS.
4.2 `sampling` decreased. Expect: all MUST-HIT, remainder NO-JOB.
4.3 `sampling_factor` in a refinement module, up and down.
4.4 `seletop select` up and down.
4.5 `seletopclusts top_models` up and down.
4.6 `max_nmodels`.
4.7 Increase then decrease back to the original — B must be identical to A.

The property under test is *prefix stability*: whether the job schedule is
generated so that job k is a function of k alone, independent of the total.
A schedule that distributes work by, e.g., `sampling / n_combinations` breaks
this — changing the total renumbers everything.

Adjacent MUST-MISS probe: `sampling` unchanged but `iniseed` changed.

---

## Axis 5 — Selection membership and ordering

Clustering and selection modules **choose** structures; they do not transform
them. A structure surviving into B's refinement stage is bit-identical to the
one in A — but it may arrive under a different filename, at a different index,
in a different cluster, at a different rank. This axis tests whether job
identity tracks **content** or **name/position**.

It is where the largest reuse is available: refinement is the most expensive
stage, and moderate clustering changes retain most of the selected set.

Sub-cases, in increasing difficulty:

5.1 Same structure, same rank, same filename. → MUST-HIT (trivial baseline).
5.2 Same structure, same rank, **different filename**.
5.3 Same structure, **different rank**, same filename.
5.4 Same structure, different rank **and** different filename.
5.5 Structure selected in A, not in B. → NO-JOB.
5.6 Structure selected in B, not in A. → MUST-MISS.
5.7 Same structure reached by a **different selection route** — e.g. via
    `seletop` in A and `seletopclusts` in B.
5.8 Whole selected set identical, order reversed (`sort_ascending` flipped).
5.9 One structure inserted near the top, shifting every subsequent rank by one —
    the maximum-damage version of 5.3.

Concrete generators: clustering cutoff moved slightly (`clustfcc` fcc cutoff,
`clustrmsd` cutoff); `min_population` changed; `clustfcc` swapped for
`clustrmsd`; an upstream sampling change (Axis 4) reshuffling the score-ranked
order; selection count changed (overlaps Axis 4, but the interest here is the
*renumbering it induces downstream*, not the count).

### The apparent crux, and why it is not one

A model's rank is not obviously a pure label: refinement assigns per-model
restraint files **by position**, and models carry seeds. So a reorder looks like
it could be either

- (a) cosmetic — same structure, same restraints, same seed; or
- (b) genuinely different science — the reorder changed which restraint file the
  model is refined against,

and these look indistinguishable from outside. They are not. Under content
addressing the distinction is automatic and requires no policy:

- In (a) the model content, the restraint content, and the seed are all
  unchanged, so the read-set is unchanged and the key is unchanged. **HIT.**
- In (b) the job reads a *different restraint file's content*, so the read-set
  differs and the key differs. **MISS.**

The restraint assignment is not metadata *about* the job — it is one of the
job's inputs, and it enters the key as content. Nothing needs to detect intent.

**What actually remains is narrower and mechanical:** for every per-model
quantity, decide whether it is a **locator**, a **binding**, or an **input**.

| Quantity | Nature | Belongs in the key? |
|---|---|---|
| real output filename (`flexref_7.pdb`) | locator | no — erased |
| real input model filename | locator | no — erased |
| **the canonical pin the model binds to** | **binding** | **yes** |
| model content | input | **yes** |
| restraint file content | input | **yes** |
| seed | input (travels with the model) | **yes** |
| index / `count`, *where it selects a file* | locator for that file | no — the file's content already is the identity |
| index / `count`, *where it reaches the science any other way* | input | **yes** |

The binding row is the one that makes this axis testable rather than
tautological. The property to prove on 5.2–5.9 is **not** that names are
ignored — it is that the **name → pin mapping is invariant** under the
perturbation. Two cases with opposite answers:

- A refinement job takes one input model, so that model binds to the same pin
  whatever its rank. Rank is pure locator. **5.3 can hit, and must.**
- A rigidbody job takes several molecules bound to ordered pins, so swapping two
  molecules rebinds them. That is genuine science, not renaming. **It must miss**
  (Axis 0b.1).

A test set that only checks "the reordered job hit" would pass equally well
against a key that ignores binding altogether — which is why 0b.1 has to sit
adjacent to 5.3.

The `count` row is the one to settle per module rather than globally. Where
`count` picks a per-structure restraint file, it is a locator sitting next to
the identity it locates, and keeping both lets the locator invalidate a key that
the identity says is unchanged — which is precisely how a false miss on 5.3/5.9
is manufactured. Where `count` influences the computation directly, it is a
genuine input and must stay.

Consequences for the test set:

- 5.2–5.9 are **gated MUST-HITs**, not ACCEPTED-MISS. Every miss on this axis is
  a locator leaking into the key — a specification violation, not a scope
  boundary.
- Each of 5.2–5.9 still needs both a restraint-free and a per-model-restraint
  variant — no longer to disambiguate intent, but to prove the restraint content
  is genuinely in the key and the restraint *position* genuinely is not.
- Add a seed-provenance case: a seed derived from downstream position rather
  than inherited from the originating model would make every reorder a miss,
  silently and correctly-looking.
- Residual, and genuinely narrow: if a per-model restraint file's *content*
  embeds the model index (a comment line, a header), then identical restraints
  differ in bytes and 5.3 becomes a false miss. That is a content-hygiene defect
  in the file writer (the 6.12/6.13 family), not a flaw in the identity model.

This is the same locator-versus-input test that Axis 6.16 applies to version
stamps and Axis 12 applies to artifact paths — with Axis 0b supplying the third
category, *binding*, that keeps the test from over-shooting into "names never
matter". One principle, four places.

---

## Axis 6 — Input content

Content that CNS reads changes. Verdict: **MUST-MISS for every job reading the
changed file; MUST-HIT for every job that does not.** The test asserts the
*precise boundary*, not merely that something missed.

6.1 One input molecule's coordinates changed.
6.2 An ensemble member added.
6.3 An ensemble member removed.
6.4 Ensemble members reordered within one PDB.
6.5 Molecule order changed in `molecules = [...]`.
6.6 A restraint file's content changed (`ambig`, `unambig`, `hbond`).
6.7 A restraint file swapped for a different file with identical content.
    → **MUST-HIT** (content, not path).
6.8 A force-field/toppar file changed.
6.9 A module CNS template changed.
6.10 The CNS executable changed.
6.11 A ligand topology/parameter file changed.

**Semantically-null edits** — their own group, because this is where MUST-MISS
and ACCEPTED-MISS collide and each must be classified deliberately:

6.12 A PDB `REMARK`/comment line changed.
6.13 Whitespace or line-ending change in a restraint file.
6.14 Chain/segid renamed consistently throughout.
6.15 A file recompressed (different bytes, same content).

**6.16 A different HADDOCK3 version.** This is *not* a separate kind of
perturbation and *not* an integrity concern. A HADDOCK3 upgrade is a bundle of
Axis 6 and Axis 7 changes applied at once: some CNS templates changed, some
toppar files changed, some parameter defaults reaching the `.inp` changed, and
most of the tree did not. Its verdict is therefore not a blanket invalidation
but a **precise partition, derived automatically from content** — which is the
useful result, since the miss set *is* the blast radius of the upgrade.

A version stamp in the key would destroy exactly this, replacing a
content-derived answer with a proxy. That is the same error as keying on
filenames instead of content, which Axis 5 exists to catch. Version is a
locator for a body of code; the checksum set is its identity.

The real hazard people attribute to cross-version reuse — "the new version
changed the computation but the checksum did not notice" — is not a version
problem. It is Axis 8.

---

## Axis 7 — Scientific parameters

Parameters that reach the `.inp`. Verdict: **MUST-MISS**, localised to the
modules that read them.

7.1 A parameter read by exactly one CNS module.
7.2 A parameter read by several CNS modules.
7.3 A global scientific parameter (`iniseed`).
7.4 A parameter changed and changed back — B must equal A.
7.5 A parameter whose *default* changed while the config stayed byte-identical.
    Verdict follows from Axis 6.16: if the default reaches the `.inp`, the
    canonical script differs and it is an ordinary MUST-MISS. Nothing special
    is required, and nothing special should be added.

---

## Axis 8 — Completeness of the declared read-set

The axis that under-declaration hides in, and the substance behind the fear
usually misfiled as "old version". If some influence on the result is *not* in
the checksummed read-set, then the key is unsound — not across versions, but
**within a single version**, and every hit it has ever served was already
suspect.

Verdict throughout: **MUST-MISS**. A hit here is the catastrophic failure.

8.1 An ambient environment variable that CNS resolves.
8.2 A file reached by an include path the scanner does not follow.
8.3 A file reached only under a conditional branch not taken during scanning.
8.4 A file reached by a dynamically-constructed name.
8.5 An optional/guarded reference that is present in one run and absent in another.
8.6 Locale, timezone, `ulimit`, or other process state reaching the output.
8.7 Working-directory-relative reads.
8.8 Anything read through a symlink whose target changed.

**The strong form of this test is not a perturbation pair at all**: execute a
job in an environment containing *only* its declared dependencies. Anything
missing makes CNS fail there, converting a silent key defect into a loud one.
This is worth more than any number of pairwise cases, because it tests the read
set against reality rather than against a guess.

---

## Axis 9 — Interruption state of the source

Here the perturbation is applied to A, not to B: A is left incomplete, and B is
the same config re-run against it. The variable is *where the knife fell*.

9.1 Between steps, cleanly.
9.2 Mid-step, some jobs complete. → per-job, not per-step, recovery.
9.3 Mid-job, while CNS was writing its output (torn/truncated file).
9.4 Mid-job, after CNS finished writing but before the result was recorded.
9.5 After the result was recorded but before post-processing/normalisation.
9.6 During the record write itself (torn record).
9.7 Hard kill (SIGKILL) vs graceful (SIGINT) — different amounts of flushing.
9.8 A ended by exceeding its faulty tolerance.
9.9 A contains individually-failed CNS jobs alongside successful ones.
9.10 A was never completed — B extends past where A stopped.

Verdict: **MUST-HIT for every job whose result is complete and recorded;
MUST-DEGRADE for every job in a partial state.** No interruption point may
produce a hit on an incomplete artifact. This is the axis where a correctness
failure is most likely, because the failure states are transient and hard to
reach on purpose — it needs fault injection, not luck.

Note that under the layer separation, **resume and reuse are the same
operation**, differing only in whether the cache being read is also being
written. This axis is Axis 11 with that one bit flipped; test it as such rather
than as a special mode.

---

## Axis 10 — Conflict discrimination

Two records claiming the same job key with different results. This is **not a
bookkeeping nuisance to be resolved by precedence or by erroring out**. It is
evidence, and it is the only evidence the system gets for free.

`job → result` is supposed to be a function. Two results mean exactly one of two
things, with opposite implications:

| | Cause | What is wrong | Remedy |
|---|---|---|---|
| **(a)** | the computation is nondeterministic | the *artifact* (Axis 0) | better normalisation, or drop to witness-level equality |
| **(b)** | the key is under-specified | the *key* (Axis 8) | fix the read-set — and every past hit is suspect |

Collapsing these is wrong in both directions: erroring on (a) makes the cache
unusable for any residually-nondeterministic artifact, while
picking-by-precedence on (b) silently ships wrong results indefinitely.

So the test set must **construct both kinds deliberately** and assert they are
told apart:

10.1 Conflict from a volatile field surviving normalisation. → class (a).
10.2 Conflict from an undeclared dependency (an Axis 8 probe). → class (b).
10.3 Conflict where the two results agree at witness level but differ bitwise.
10.4 Conflict between success and failure for the same key.
10.5 Duplicate records that **agree** — must be silent, and must be counted.
10.6 Conflict discovered across merged caches rather than within one.

10.5 is the point worth carrying: **every agreeing duplicate is a passed
reproducibility test, arriving free of charge during ordinary use.** A design
that suppresses duplicates throws away its own witness data. The caching
feature is, incidentally, a continuously-running reproducibility monitor — which
is the strongest link between the postponed testing work and the accepted
caching work.

---

## Axis 11 — Result-cache topology

The result cache is `job checksum → result checksum`: pure metadata, path-free,
tiny, and **meaningful with no bytes present at all**. It merges by set union.
Conflicts on merge are Axis 10; nothing else about merging is interesting.

This means the questions here are about **coverage**, not about lineage or
provenance of runs:

11.1 One cache, full coverage. → all MUST-HIT.
11.2 Two caches, disjoint coverage. → union MUST-HIT.
11.3 Two caches, overlapping and agreeing. → MUST-HIT from either.
11.4 Coverage from a cache produced by an unrelated workflow that happens to
     contain matching jobs. → MUST-HIT. Workflow relationship is irrelevant;
     only key presence matters.
11.5 Coverage from a superset workflow (B is a prefix of A).
11.6 Coverage from a producer that is not a HADDOCK3 workflow run at all
     (`haddock3-score`, a synthesized job, a hand-built CNS job).
11.7 Zero overlap. → all MUST-MISS, no spurious hits.
11.8 The current run's own cache used as a source (this is Axis 9, resume).
11.9 **A cache with no artifact store behind it at all.** A valid and useful
     state: the names of answers are known, their content is not. It must not
     be treated as corruption. It supports cost preview and job-level run
     diffing, and it is the state a *published* cache would arrive in.
11.10 A cache published/transported separately from any run directory.

Malformed *records* belong here too, and are strictly a parsing question with
nothing to do with artifacts:

11.11 Truncated, blank, or wrong-arity record.
11.12 Non-checksum-shaped key or result.
11.13 Duplicate key, identical result (must be accepted; see 10.5).

Transitivity (B reads A; C reads B) is **not** a distinct case under this model:
records are a set, and reuse does not decay across generations. Its presence in
an earlier draft was a run-directory-lineage assumption, not a content-addressed
one. Keep one case to prove the non-decay, not a family.

---

## Axis 12 — Artifact-store resolution

The store is `result checksum → bytes`. Location-independent, verifiable on
read, plural. A path here is a **locator, not part of identity**: bytes found at
*any* location that hash correctly are correct; bytes that hash wrongly are
rejected wherever they came from.

That collapses the whole "integrity" family into exactly two outcomes, both
safe by construction — **bytes not found**, and **bytes found but wrong
checksum**. Both are MUST-DEGRADE. There is no third case, and in particular
there is no case in which a bad locator yields a wrong result.

12.1 Artifact deleted.
12.2 Artifact modified in place.
12.3 Artifact truncated.
12.4 Artifact replaced by a same-size file.
12.5 Artifact replaced by a directory or symlink.
12.6 Artifact unreadable (permissions).
12.7 Artifact compressed or recompressed. → **MUST-HIT** (content, not encoding).
12.8 Artifact **outside the producing run directory** — a shared store, an
     archive on another disk, a hardlink farm, a colleague's run, a relocated
     run. → **MUST-HIT.** Containment is a policy about trust and lifetime, not
     about correctness; enforcing it forbids exactly the useful topologies and
     buys nothing that checksum verification does not already provide.
12.9 Two stores, one holding the bytes. → resolve from either.
12.10 Two stores, one holding *wrong* bytes under the right checksum. → reject
      that one, resolve from the other; a poisoned store must not deny service.
12.11 No store holds the bytes, but the record exists (11.9). → MUST-DEGRADE,
      not an error.
12.12 Store on a different filesystem (forces copy rather than link).

---

## Axis 13 — Job-shape coverage

Orthogonal to every axis above: each of Axes 0b–8 must be exercised against each
distinct job shape, because a shape-specific defect hides in a shape-agnostic
test set.

- `topoaa` / `topocg` — two outputs (PDB + PSF), one input molecule.
- `rigidbody` — many input molecules, per-job restraint file, per-job seed.
- `flexref` / `emref` / `mdref` — one input model carrying an inherited seed and
  a per-model restraint file.
- `emscoring` / `mdscoring` — scoring rather than sampling.
- `cgtoaa` — coarse-grained ↔ all-atom conversion.

Plus the non-cacheable neighbours, to confirm they are correctly excluded rather
than silently mishandled: analysis modules, OpenMM, and non-local execution
modes.

---

## Composition

Real modifications are rarely single-axis. Once the single-axis matrix passes, a
small number of composed cases is worth more than more single-axis ones:

- Axis 4 + Axis 5: raise `sampling`, which reshuffles ranks downstream.
- Axis 2 + Axis 5: insert a module *and* change clustering.
- Axis 6 + Axis 5: change one input molecule, changing both the job that reads
  it and the selection order of everything downstream.
- Axis 9 + Axis 4: interrupt A, then resume with a *larger* sampling in B.
- Axis 6.16 + Axis 5: upgrade HADDOCK3 (partial invalidation) across a
  reordering — the case where a content-derived miss set and a name-derived one
  are hardest to tell apart.
- Axis 11 + Axis 12: a cache whose records are complete but whose store is
  partially populated from several places.

The value of composed cases is that they are the ones where a per-axis fix that
happens to be position-dependent stops working.

---

## Structural invariants versus empirical tests

With the implementation being written fresh, the primary question for each axis
is not "how do I test this?" but **"can I build it so this cannot happen?"** A
structural invariant is worth more than any number of cases, because it holds for
inputs nobody thought to enumerate.

**Dischargeable by construction** — build it this way and the axis reduces to a
one-line assertion:

| Axis | Structural choice that discharges it |
|---|---|
| 1 (location), 2 (step position) | Canonicalization completeness asserted at key-construction time: no run path, install path, or step-folder token may survive into the canonical form. Fail loudly if one does. Then Axes 1 and 2 are corollaries, not test families. |
| 0b.9 (unclassifiable reference) | Make an unclassifiable reference a **hard error**. A fallback bucket that embeds an absolute path is the mechanism by which the key silently becomes location-dependent; refusing to have one removes the failure mode rather than testing for it. |
| 11.9 (cache with no bytes), 12.8 (bytes anywhere) | **Separate the result cache from the artifact store.** Once records are `job → result` with no paths, a byte-free cache and a remote/shared/relocated store are the *normal* modes, not edge cases needing defence. |
| 12.1–12.6 (artifact integrity) | Verify by checksum on every read, always. Then "corrupt artifact" has exactly one behaviour, and the six cases are one test. |
| 0b.7 (canonicalization rule drift) | **Golden canonical forms**, generated as a build artifact: freeze the canonical script and pin table for one job of each shape. A rule change then surfaces as a reviewable diff instead of a global cache miss discovered months later. |
| 3 (orchestration parameters) | Construct the CNS parameter set by **explicit inclusion** rather than by copying the module params and deleting known-irrelevant keys. Then a new orchestration parameter cannot leak into the key by default, which is the direction that matters. |
| observability | Emit an explicit per-job **executed-vs-reused** signal. Not a test-harness concern — a requirement on the implementation, because without it most of this matrix is unassertable. |

**Irreducibly empirical** — these cannot be designed away and are where the real
cost sits:

| Axis | Why it must be measured |
|---|---|
| **0** — determinism of CNS | A property of CNS and the platform, not of the cache. Must be measured before anything may be checksum-gated. |
| **8** — read-set completeness | The scanner's claim about what a job reads can only be checked against reality. The strong form — execute a job with *only* its declared dependencies present — is the single highest-value test in the document and needs a harness built for it. |
| **5** (generators) | Requires real multi-model runs reaching a selection module with enough models for clustering to be meaningful. The *binding* half of Axis 5 is structural; the reuse-fraction half is not. |
| **9** — interruption states | Transient by nature. Needs deliberate fault injection; will not be reached by luck. |
| **10.1–10.2** — conflict classes | Both kinds must be manufactured on purpose. |

The shape of the answer: **most of the catastrophic-failure surface is
structural, and most of the remaining empirical cost is CNS execution time.**
Test-writing effort is no longer the scarce resource; CNS wall time and the
judgment calls in "Open decisions" are.

---

## Build order

Each stage makes the next interpretable.

1. **Decide the open decisions below.** Several are load-bearing on the
   architecture (notably layer separation) and are expensive to retrofit.
2. **Build the observability signal and the canonicalization completeness
   assertion first.** They are the two things that make everything else either
   testable or unnecessary.
3. **Axis 0b as unit tests over canonical mappings** — no CNS, no runs. This is
   where the discriminating power is cheapest.
4. **Axis 0**, to establish what may be gated by checksum at all.
5. **MUST-MISS probes across 0b, 6, 7, 8 — before any MUST-HIT case.** A key
   that is too invariant passes every MUST-HIT test perfectly, so the
   catastrophic direction must be pinned first. Most of it is reachable without
   running CNS: if two jobs that must differ produce the same key, that is
   visible in the mapping alone.
6. **Axis 8's isolated-execution harness**, which also validates the dependency
   scan against reality.
7. The MUST-HIT axes 1–4, most of which should by then be corollaries.
8. **Axis 5**, once 0b.1 and the Axis 4 prefix cases are green.
9. Axes 9–12 — the cache as an object rather than the key.
10. Axis 13 as a multiplier, composed cases last.

---

## Open decisions

Design choices the new implementation must make deliberately. A test that "finds
out" what the system does here is recording an accident. Items 1–5 are answered
by this taxonomy and the answer should be built in; 6–7 require measurement or a
call from the user.

1. **`count` / index, per module.** Where it selects a per-structure restraint
   file it is a locator for that file, and the file's content is already the
   identity; where it reaches the science otherwise it is a genuine input.
   Requires a per-module verdict, not a global one. (Axis 5)
2. **Canonicalization scheme versioning.** Either the naming rule is stable by
   construction and frozen by golden canonical forms, or it is explicitly
   versioned in the key. Doing neither means a future refactor silently
   invalidates every cache in existence. (Axis 0b.7)
3. **Conflict policy.** What the system does on class (a) versus class (b)
   conflicts, and how it tells them apart. Not "precedence or error". (Axis 10)
4. **Whether the result cache and artifact store are separated.** A record that
   carries a path fuses them, and the fusion is what makes locator questions
   masquerade as integrity questions. Separation is what makes 11.9 (a cache
   with no bytes) and 12.8 (bytes anywhere) expressible at all. **This is the
   one architectural decision that is expensive to retrofit** — the previous
   implementation fused them, and most of the confusion this taxonomy corrects
   traces back to that. Decide it first. (Axes 11, 12)
5. **Whether agreeing duplicates are counted or discarded.** Counting them makes
   the cache a continuously-running reproducibility monitor at zero cost;
   discarding them throws that away. (Axis 10.5)
6. **Which artifacts are gated by checksum and which only at witness level**,
   determined by Axis 0 rather than assumed.
7. **The declared boundary of what is cacheable at all** — analysis modules,
   OpenMM, and non-local execution modes are currently outside it. The test set
   should confirm they are *excluded* rather than silently mishandled, which is
   a different assertion from "not tested". (Axis 13)
