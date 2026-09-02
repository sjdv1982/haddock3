# Proposal: corrections to the Stage 2 behavioural contract, and the Stage 1 changes they imply

## Standing of this document

This note proposes changes to the caching test suite at `end-to-end_tests/caching/`
— its cases, their fixtures, and the expectation resolver — which is the whole
of Stage 2 as described in `caching-publication-plan.md`. It is a proposal, not
a record: nothing here has been applied.

Sections A to D are Stage 2 alone. Section E is a policy the suite gets wrong
and whose key lives in Stage 1, so it is corrected in both places. Section F
records a case that cannot be declared until a Stage 1 decision is taken, and
section G proposes the Stage 1 changes those two imply — in the
canonicalization, checksumming and keying machinery, together with the job
schedule that feeds it. Stage 1 is reached only where a Stage 2 assertion is
otherwise inexpressible or would have to be satisfied by making the feature
worse; this document does not reopen Stage 1 generally.

Out of scope: the axes and their taxonomy, the hardlink measurement, the
no-mocks constraint, the corpus systems, and the budget policy. None of the
defects below are arguments against any of those.

Nothing in the suite is shipped behaviour, so it can be rewritten at will —
cases, fixtures, resolvers and verdicts alike. What matters is the *direction of
justification*, because Stage 2 exists so that the contract is not quietly
rewritten to describe whatever the implementation happens to do. Correcting a
case because it contradicts its own prose, or asserts the opposite of a stated
policy, is legitimate; adjusting one because an implementation disagrees with it
is the failure mode the staging is there to prevent. Every correction below is
argued from the case's own `why`, from measured artifact bytes, or from what the
CNS recipes actually read — never from what the current implementation does.

## How these were found, and what that says about the suite

Running the Stage 3 implementation against the Stage 2 suite produced failures.
On investigation, all but one were defects in the contract rather than in the
implementation.

Freezing the contract before the implementation exists is the right way round
and this document does not argue otherwise. But it has a hazard worth naming:
**a suite written first can only be wrong where nothing yet pushes back.** Every
defect below sits in a resolver, a fixture, or a `patterns` line; not one sits
in the prose. Several cases state the correct rule in their own `why` and then
assert its opposite three lines further down, and nothing but an implementation
disagreeing with them was ever going to surface that.

The second hazard is what the failures invite. For each defect there is a cheap
implementation change that turns the suite green and makes the feature worse:
make topology reuse position-sensitive, so member 1 always comes from member 1;
force a duplicate job to miss; put the upstream job's identity into the
downstream key. Each of those is a step from a content-addressed cache toward a
dependency-graph one, and each would be adopted for the best of reasons — a red
test. The reflex to resist is bending the key to the assertion.

## The one-family constraint, which spans all three stages

`flexref`, `emref` and `mdref` are **one job shape**, and the suite says so:
Axis 13 declares them "one input model with an inherited seed" and, budget
being the stated limit, tests the shape through a single representative. Axis
4.3 perturbs `sampling_factor` on flexref alone; Axis 13.2 changes a weight on
flexref alone. No case exercises the same claim on emref or mdref, and none
should be added — testing one member *is* testing the shape.

That is sound coverage only while the premise holds. The consequence is a
constraint on the code, in every stage:

> **A change to the job emission, seeding, indexing, or cache participation of
> one of `flexref`/`emref`/`mdref` must be applied to all three, or the
> divergence must be deliberate and written down.**

Violate it and the suite does not go red. It stays green while two of three
modules carry the defect, because the representative has quietly stopped
representing. That failure is strictly worse than a coverage gap: a gap is
visible in the case list, and this is visible nowhere.

The constraint is live right now. The Stage 3 working tree reorders flexref's
replica emission into rounds and leaves emref and mdref emitting input-major —
so after that change the three modules disagree about what `<module>_k.pdb`
means, and Axis 4.3 continues to pass. §G.2 proposes fixing all three in Stage
1, which is where the family is already maintained: the `sampling_factor` clamp
commit changes emref and mdref together, and the seed-derivation commit changes
the five `$seed` modules together, arguing explicitly that fixing one of two
coupled sites would be worse than fixing neither.

It binds the Stage 3 diff just as tightly, and less obviously. Anything the
caching implementation decides per module — how the engine and cache context
are wired, what a module publishes and when, how tolerance and faulty models
are handled on a cache hit, any short-circuit or special case keyed on a module
name — has to be identical across the trio for the same reason. A per-module
special case in the cache path is this same defect one layer down, equally
invisible, and equally green.

(Do not confuse this shape class with `PER_MODEL_MODULES` in
`cachesuite/expectations.py`, which spans six modules. That set answers a
different question — which modules consume exactly one model, so that the
oracle can pair output *i* with input *i*. The one-family constraint is about
the refinement trio specifically.)

## The three kinds of defect, and the remedy for each

| Kind | Symptom | Remedy |
|---|---|---|
| **Oracle** | The case's prose states the right rule; the mechanized expectation contradicts it. | Fix the resolver. No new cases — duplicating here would preserve a bug as a "variant". |
| **Fixture** | The perturbation does not produce the situation the case describes, so the case passes or fails for a reason unrelated to its claim. | Split into a pair: one half keeps the declared outcome and changes the mechanics, the other keeps the mechanics and changes the outcome. |
| **Normative** | The declared outcome is wrong as policy. | Reverse the declaration, and check whether the key must change with it — if so the correction is Stage 1's, not Stage 2's. |

The pair construction is worth applying only where the two halves assert
*different rules*. A second fixture that produces the same verdict by another
route is coverage, not contract, and the suite's budget is better spent
elsewhere.

This is not a new idiom. The suite already pairs 6.6 with 6.7 and 6.12 with
6.13, and 6.12's prose does the part that matters: it names its twin and states
what separates them ("not how meaningful the edit looks but whether it survives
into the read-set"). New pairs should carry the same cross-reference. A pair
teaches the rule; a single case teaches a verdict; the rule is what Stage 2 asks
users to sign off on.

---

## A. The `auto` verdict identifies source entries by name (oracle)

**Affects** 6.4, the sampling half of 6.2 and 6.3, and composed 6×5.

For any module outside `PER_MODEL_MODULES`, `_find_sources` falls back to
`_same_slot`, which matches module + occurrence + **basename**
(`cachesuite/expectations.py`). That identifies a job by its name. The feature
identifies a job by its content. Axis 6 exists to decouple those two, so in
precisely the cases that matter the oracle asks the wrong question.

Observed, from the recorded runs against `corpus/base/pp`:

| | target | source | oracle demands |
|---|---|---|---|
| 6.4 `hpr_ensemble_1_haddock.pdb` | `6632e161` | member 2 is `6632e161`, member 1 is `cc999a33` | hardlink to `cc999a33` |
| 6.2 `rigidbody_3.pdb` | `5e399f89` | slot 3 holds `9c5f4d7c` | hardlink to `9c5f4d7c` |
| 6.3 `rigidbody_10.pdb` | `455a1fef` | slot 10 holds `17648bfc` | hardlink to `17648bfc` |

`_same_slot` is also wrong in the other direction. It decides not only *which*
source entry a job may come from but *whether* one exists at all, and it
decides that by name and plausibility. So wherever a same-named source file
exists but its content changed, `auto` reports a hit and the case demands a
stale result. Composed 6×5 is that failure: it moves one atom in
`e2aP_1F3G.pdb`, its prose says "everything that reads the changed molecule
must miss", and its `auto` verdict then requires the changed topology and all
forty rigidbody outputs to hardlink their source counterparts. `auto`'s own
docstring states the rule it does not implement — "the source either holds a
usable entry for this job or it does not, and which it is follows from
content".

6.4 is the serious one, because it does not merely fail a correct
implementation: **it can only be passed by an incorrect one.** The only way to
satisfy it is to serve member 1's slot from a source with different
coordinates — "it reused the wrong model", which the suite's own README names
as the dangerous failure and the reason results report *which* source they came
from. A test whose sole passing implementation is the unsafe one is worse at
that spot than no test.

Two traps for whoever fixes this. First, PSFs are conformer-independent
(members 1 and 2 are both `9c0791c5`), so half of 6.4's artifacts pass
regardless; a green `.psf` is not evidence. Second, 6.3's topology half passes
only because `_edit_models` removes the *last* member and leaves the names
aligned — see §C.

**Proposed remedy.** A content-based resolver for topology and sampling
artifacts, in the shape of the existing `_by_refinement_input`: identify the
source entry by the content of what the job reads — the split member
`hpr_ensemble_i.pdb` that topoaa writes beside its outputs, the model
combination and seed for rigidbody — and never by the artifact's own name. It
must return *no* source when nothing matches by content, which is what makes
composed 6×5 and 6.1-style content edits come out as misses. And it must permit
many-to-one, since after §C two target artifacts can correctly hardlink to one
source file.

No case text changes and no case is added. 6.4's `why` already states the rule
the new resolver implements.

---

## B. Byte-identical duplicate jobs are declared MUST-MISS (oracle)

**Affects** 6.2 (the added member) and 4.3a (the added replicas).

Both cases declare, through `auto`, that a job must be recomputed because no
*named* source slot corresponds to it — while its content is byte-identical to
a job the source already holds. A content key correctly serves both from the
existing entry.

In 6.2 the added ensemble member is a copy of member 1 (`perturb.py` appends
`blocks[0]`), and member 11's topology is `cc999a33`, the same bytes as member
1's. In 4.3a the effect is structural rather than accidental: a refinement
model inherits its seed from its input model (`libcns.prepare_expected_pdb`
copies `model_obj.seed`), so every replica of one input is the same computation.
Measured on the recorded run: `flexref_1.pdb` and `flexref_3.pdb` are both
`2a931732`, `flexref_2.pdb` and `flexref_4.pdb` are both `0b8db467`.

This matters beyond the two cases, because 4.3a is not fixed by re-ordering
flexref's replica emission. Under round-major emission the later rounds are
still byte-identical to the earlier ones — reordering moves them, it does not
make them different computations. **If the implementation forces a duplicate
occurrence to miss in order to satisfy the oracle, that behaviour should be
removed rather than kept**: it is the clearest instance of bending the key to
the assertion, and §G.3 removes the duplicates at their source instead.

**Proposed remedy.** Covered by the content-based resolver of §A for 6.2. For
4.3a, the per-model resolver must map an output to the input it refines by
content rather than by `position < len(inputs)`, so that replica outputs
resolve to the same input as their first round. Both then derive the correct
mapping automatically, which is what `auto` is for.

What 4.3a should assert follows from the seeding rule in §G.3, not from the
current behaviour: once a replica's seed is derived from its input and its
replica index, later replicas are genuinely new work and must miss (§F). The
duplicate-resolution requirement stands regardless, because 6.2b is a genuine
duplicate whatever the seeding scheme does.

---

## C. Fixtures that do not produce the situation the case describes

**6.2 — "an ensemble member added" adds a duplicate.** The case's `why` says
"only the new one is new work". With a byte-identical copy of member 1 there is
no new work at all, and the case tests deduplication under a title that
promises something else. Both behaviours are worth pinning, so this is the
pair construction:

- **6.2a — a distinct conformer added.** The fixture adds a genuinely new
  member. Existing members hit, the new one misses. This is the case the
  taxonomy names, and the first time it will actually have been tested.
- **6.2b — a byte-identical member added.** The current fixture, with the
  honest verdict: member 11 hits, served from member 1's entry. Its `why`
  should say what separates it from 6.2a, in 6.12's manner.

**Composed 6×5 — the molecule it changes is in every job.** The case promises a
composition: some jobs miss on content while the survivors are reordered, "so a
content-derived miss set and a name-derived one are hardest to tell apart
here". But `e2aP_1F3G.pdb` is the non-ensemble molecule, present in every
rigidbody combination, so once it changes there are no survivors to tell apart
— the run misses from the changed topology job downward, and the reordering
half of the composition has nothing to bite on.

Move the same coordinate edit to `hpr_ensemble.pdb`. `_edit_coordinate` moves
the first ATOM record it finds, which lies in member 1, so exactly one conformer
changes: its topology misses, the rigidbody jobs that use it miss, the other
nine members' jobs survive, and the clustering downstream reshuffles their
ranks. That is the case the prose describes, and it is the one that
distinguishes a content-derived miss set from a name-derived one. This is a
replacement fixture, not a pair.

**6.3 — "an ensemble member removed" removes the last one.** Dropping
`blocks[-1]` keeps every remaining name aligned with its content, so the
topology half of the case passes without exercising the renumbering it claims
to test. Change the fixture to remove a *middle* member. This is not a pair:
the current fixture asserts nothing the new one does not, so it is a
replacement, not a twin.

---

## D. An inert upstream change is declared to invalidate everything downstream

**Affects** 6.8 and 6.16. Both are normative in appearance and fixture defects
in substance: each declares one half of a rule whose other half is missing, and
the half it declares is the wrong one for the fixture it uses.

The rule at stake is **convergent recomputation**: an edit costs exactly the
jobs that read the edited file, and stops there when their recomputed artifacts
come out identical. It is what makes an inert upgrade cheap, and it is the
single most valuable thing the feature does. 6.16's own `why` argues for it
precisely — "a precise partition derived automatically from content... a
version stamp in the key would destroy exactly this, replacing a content-derived
answer with a proxy" — and then its `patterns` hard-code the proxy.

Observed:

- **6.8**, appending a comment to `protein-allhdg5-4.top` on the `tiny` base:
  every artifact is byte-identical, topoaa and rigidbody alike. That file is
  referenced only by the topology recipes
  (`topoaa/cns/generate-topology.cns`); rigidbody reads `.param` files and
  never `.top` (`rigidbody/cns/read_param.cns`). The correct blast radius is
  the topology jobs alone. The case declares `default: miss` with
  `misses: {topoaa: 4, rigidbody: 4}`.
- **6.16**, appending a comment to `emref.cns` on the `refine` base: emref
  recomputes to the same bytes (`04c17ba7`, `85f25f4b`) and mdref's inputs,
  script and parameters are all unchanged (`57a7a155`, `9b16a563`). mdref must
  hit. The case declares `{match: "*_mdref/*", expect: miss}`.

The declared cascade is not merely unnecessary; it is unreachable for a
correct implementation. The only way to produce it is to put the upstream
job's identity, rather than its output content, into the downstream key — the
version stamp arriving through the back door.

**Proposed remedy — two pairs, each stating the rule in both directions.**

- **6.8a** keeps the mechanics: the `.top` comment. topoaa misses, rigidbody
  hits, the glycan job stays unasserted as it is now.
- **6.8b** keeps the outcome: the same edit applied to
  `protein-allhdg5-4.param`, which rigidbody genuinely reads. Both stages miss.
- **6.16a** keeps the mechanics: the inert comment. emref misses, mdref hits.
- **6.16b** keeps the outcome: a *substantive* edit to `emref.cns` that changes
  what emref computes. emref misses and mdref misses — derived by content, not
  declared by cascade.

Neither half alone is worth much. 6.8a on its own cannot distinguish
"correctly outside the read set" from "under-declared key", since both show up
as a hit; 6.8b is what closes that, in the same way 6.7 closes 6.6. And 6.16a
alone would look like a claim that upgrades never invalidate anything, which is
exactly what 6.16b refutes.

Two further notes. The middle assertion in each pair — that the recomputed
upstream artifact is byte-identical to the source run's — is worth making
explicit, because it is the only end-to-end check the suite has on Stage 1's
artifact normalization: if run-volatile provenance leaked back into a CNS
output, the downstream hit would silently evaporate and both pairs would start
failing for a reason neither names. And 6.16b's fixture must be **verified** to
change the output bytes, with the verification recorded in its `why`; a
substantive-looking edit that turns out inert would silently become a second
copy of 6.16a asserting the opposite verdict — the failure mode §C describes.

---

## E. The CNS executable is not part of job identity (normative)

**Affects** 6.10, and the canonicalizer in Stage 1.

6.10 points `cns_exec` at a shell wrapper that `exec`s the real binary and
declares `default: miss` — 8 artifacts recomputed. Its `why` is explicit that
the wrapper "computes exactly the same result", and treats that as the reason
the case is discriminating: only a key that reads the executable's content can
tell the two apart.

**This is the wrong policy, and the case should be reversed to MUST-HIT.**

The executable is not an input to the computation. It is the machine that
evaluates it. The declared computation is the CNS script together with the data
it reads; the binary is the interpreter, and a cache keyed on its interpreter
can never be shared across two installations. That is not a corner case, it is
the principal use: a lab-wide cache, a published cache accompanying a paper, a
cache carried from a workstation to a cluster. Under a content-keyed
executable, none of them ever hit, because no two installations compile or
download the same bytes.

The suite half-conceals this today. Axis 1.3 asserts reuse across a different
installation path, but it *moves* one tree, so the binary's bytes travel with
it and the case passes. The realistic scenario — a second, independently
installed HADDOCK3 — is untested, and would produce a total miss.

Nor does keying on the binary buy safety. It cannot detect a CNS build that
computes different results; it only prevents reuse between builds that agree,
which is the overwhelmingly common case. A build that genuinely disagrees is a
reproducibility problem that the cache cannot fix and should not pretend to.

The tree is currently self-contradictory on this point, which is why the
correction cannot be confined to Stage 2. Stage 1's committed canonicalizer
binds the `canonical-cns` pin to the executable's own content checksum; the
Stage 3 working tree replaces that with a policy constant
(`_CNS_EXECUTABLE_POLICY_CHECKSUM`). Stage 2's 6.10 agrees with Stage 1 and
disagrees with Stage 3.

**Proposed remedy.**

1. In **Stage 1**, bind the `canonical-cns` pin to the policy constant, with
   the reasoning above in the commit message. The pin keeps its name and its
   position, so the committed golden canonical forms do not move.
2. In **Stage 2**, 6.10 becomes `expect: {default: hit}` and its `why` is
   rewritten to state the policy rather than the opposite. There is no pair
   here: under this policy no fixture makes a miss correct, which is what
   distinguishes a normative correction from a fixture one.
3. Separately, and not as part of either stage: record the executable that
   produced each cache entry in the entry's provenance, so that a mixture of
   builds is **visible and auditable** without being part of identity. This is
   the honest replacement for the safety the current case appears to offer.

The cost of the policy should be stated plainly to the Stage 2 reviewers rather
than buried: pointing `cns_exec` at a genuinely different engine and expecting
different results is a user error this cache will not catch. Whether that
warrants an opt-in strict mode is a question for those reviewers, and this
document does not answer it.

---

## F. What the seeding rule settles for 4.3a

Stage 2's per-model oracle assumes a refinement job's identity is a function of
the input it refines and which replica of that input it is. Under the seeding
rule proposed in §G.3 that becomes true by construction, and 4.3a can be
declared rather than left open:

- the first round of replicas refines the same inputs as the source run's
  jobs and must **hit**;
- every later replica carries a different seed, is genuinely new work, and must
  **miss**;
- and the outputs stop being byte-identical duplicates of each other, which is
  what `sampling_factor` was always supposed to mean.

Written against today's code the case would have to say the opposite — later
replicas inherit one seed, so they are duplicates and must hit (§B). Both are
consistent contracts; only one of them describes a `sampling_factor` worth
having. The case follows the seeding rule, not the other way round.

The related question of flexref's replica *emission order* is separate and is
argued in §G.2. Once seeds are content-derived it no longer affects reuse at
all — only how the resulting models are numbered.

---

## G. Proposed changes to Stage 1 — the canonicalization and keying machinery

Stage 2 can only assert what Stage 1's notion of job identity makes
expressible, so several of the corrections above reach back into the
canonicalizer and into the schedule that feeds it.

One premise governs all of them, and is worth stating because it settles
questions that would otherwise look like trade-offs. **Faithfully reproducing
what HADDOCK3 produces today is not a requirement at all** — not a hard one and
not a soft one. Stage 1's purpose is to make bitwise reproducibility possible
from here on, which is a different property entirely, and no proposal below has
to justify itself against current outputs. Being conservative — disturbing as
little as we can — is nice in itself and a fair tiebreaker between designs that
are otherwise equal, but it is not a hard requirement either, and it never
outranks getting the rule right. What has to be argued is that the new rule is
principled, uniform and documented.

### G.1 Bind the `canonical-cns` pin to the policy constant

The substance is §E and is not repeated. What matters here is where the change
belongs: the canonical mapping is Stage 1's artifact, and Stage 1's committed
canonicalizer binds `canonical-cns` to `cns_exec_checksum` — the executable's
own content. The Stage 3 working tree overrides it with
`_CNS_EXECUTABLE_POLICY_CHECKSUM`, which is the right policy applied in the
wrong branch: it makes the identity of a job depend on which stage you are
standing in.

Move the constant into Stage 1, keeping the pin's name and its position in
`invariant_dependencies`. The committed golden forms are canonical *scripts*,
not checksum tables, so none of them move.

### G.2 Emit refinement replicas in rounds, in all three modules

`flexref`, `emref` and `mdref` emit every replica of input 1 before input 2, so
raising `sampling_factor` renumbers every job belonging to inputs after the
first. Emit in rounds instead — every input gets its first replica before any
input gets its second — as Stage 1 already does for rigid-body sampling.

Note what this is *not*, once G.3 lands. With seeds derived from content, the
emission order no longer affects what any job computes or whether it can be
reused; the content resolvers of §A and §B find a job wherever it sits. What
remains is legibility: `flexref_3.pdb` should mean the same thing in two runs
that differ only in `sampling_factor`, both for a person comparing runs and for
every downstream step that carries a model's number. That is worth having on
its own, and it is cheap, but it is a numbering property and should be argued
as one rather than as a reuse property.

Two constraints on the change:

- **All three modules, in one commit**, under the one-family constraint above.
  A flexref-only fix does not leave a coverage gap; it makes the representative
  stop representing, which is worse, because the suite stays green.
- **Pinned by a pure-function test**, in the shape of the rigid-body one, plus
  a CHANGELOG entry and the `sampling_factor` documentation.

### G.3 Derive every CNS seed from the job's identity, never from its position in the schedule

This is the substantive Stage 1 change, and it should become the norm for all
five modules that read `$seed` — `rigidbody`, `flexref`, `emref`, `mdref`,
`mdscoring`.

**The rule.** A job's seed is a function of `iniseed`, the content of what the
job reads, and which repeat of that job it is. It is a function of nothing
else — not of the job's index, not of the schedule's length, not of how many
molecules or conformers the run happens to contain.

**What is wrong today, and how much it costs.** Two independent defects, one
rule behind both.

*Refinement replicas are duplicates.* A refinement model inherits its input
model's seed, so every replica of one input is the same computation. On the
recorded `sampling_factor = 2` run, `flexref_1.pdb` and `flexref_3.pdb` are
both `2a931732` and `flexref_2.pdb` and `flexref_4.pdb` are both `0b8db467`,
with seeds 918 and 919 inherited from `rigidbody_1` and `rigidbody_2`.
Downstream of `rigidbody`, `sampling_factor` buys duplicate models rather than
more sampling. This is pre-existing: the pre-Stage-1 code passed
`seed=model.seed` in the same way.

*Rigid-body reuse does not survive an ensemble edit.* `rigidbody` seeds job *k*
with `iniseed + k` and binds it to `combinations[k % n]`, so both halves are
stable when `sampling` grows and neither is when `n` changes. Measured on the
`pp` base, 40 jobs over a 10-member ensemble:

| perturbation | jobs whose content survives at their own slot |
|---|---|
| add one member (6.2) | 2 of 40 |
| remove one member (6.3) | 9 of 40 |

Those are genuine recomputations, not an artefact of the oracle: a target job
can only match a source job with the same seed, the seed pins it to the same
index, and at that index the combination differs. Adding one conformer to a
ten-member ensemble therefore discards 38 of 40 docking jobs — which is close
to the worst outcome the feature exists to prevent, and it would still be the
outcome after every Stage 2 correction in this document.

A second effect compounds it: the member order presented downstream is
*string*-sorted, not numeric. The topoaa `io.json` keys of the eleven-member run
read `'0', '1', '10', …`, so an added eleventh member does not append — it
inserts between members 1 and 2 and shifts every combination after it.

**Why it is a keying question and not only a science one.** The canonical form
already erases the job index and keeps the seed: the golden forms carry
`eval ($count=canonical-count)` next to a literal `eval ($seed=917)`. **The seed
is the sole remaining channel through which a schedule's numbering reaches job
identity.** That is why emission order appeared to matter, and why no emission
order could be right in both directions — input-major numbering is stable when
the input set grows, round-major when the factor grows, and neither under both,
because the flat counter was doing work that belongs to the job's content.
Close that channel and the question disappears.

**What the identity should be made of.** The open part is not whether to do
this but what to hash:

- For refinement and scoring, `(iniseed, the input model's content, the replica
  index)`. The model's content checksum is already computed for the cache key,
  so nothing new has to be derived.
- For rigid-body sampling, `(iniseed, the combination's content, the repeat
  index of that combination)`. Deriving the combination's identity from its
  members' *content* rather than from its index in the list is what makes it
  survive the string-sort insertion above; an index would reintroduce exactly
  the fragility this change removes.

Two implementation details worth settling explicitly rather than discovering.
The derived value must land in a range CNS handles exactly — the generator this
work removed drew from 100–99999, while `iniseed` admits up to 10^16, which is
past the point where a double-precision CNS value is an exact integer, so the
derived seed should stay well below 2^53. And seed collisions between different
jobs are harmless: seeds need not be unique, only stable and distinct across
repeats of the same job, which the repeat index guarantees.

**Consequences.** `iniseed` keeps its meaning exactly — changing it still
changes every seed in the run, which is what Axis 4.8 asserts. The golden
canonical forms must be regenerated for the five `$seed` shapes, since those
forms embed a literal seed value. Results change for every seeded run: permitted
under the premise above, and a reason to make the change once and deliberately
rather than in instalments, since each instalment spends the same disruption
again. And §F is settled: later replicas become genuinely new work, so 4.3a can
be declared instead of left open.

A side benefit worth recording for Stage 4: a content-derived seed makes a job
self-contained. Its seed follows from its declared inputs, so a dumped job
carries everything needed to reproduce it, without knowing the schedule it came
from.

### G.4 What Stage 1 does *not* need to change

The pairs proposed in §D would be worthless if the canonicalizer did not
already declare the transitive read set, so it is worth recording that it does:
the scanner recurses into every resolved `.cns` reference, so
`rigidbody.inp → MODULE:read_param.cns → TOPPAR:protein-allhdg5-4.param` is
bound by content, while `protein-allhdg5-4.top` — referenced only from
`generate-topology.cns` — is not in rigidbody's read set at all. 6.8a's hit and
6.8b's miss both follow from machinery that exists. This is the one place where
a Stage 2 assertion could have implied a Stage 1 defect and does not.

### Sequencing

All three are Stage 1 commits and the later branches rebase onto them. G.3 is
the one with a design question inside it — what a job's identity is made of —
and it should be settled before G.2 is written, since G.2's justification
changes depending on whether seeds still carry schedule position. The Stage 2
corrections other than 6.10 do not depend on any of them, and can proceed in
parallel.

## How to find the rest of these

Every defect in this document sits in a resolver, a fixture or a `patterns`
line — never in the prose. A case whose machinery contradicts its own `why` is
not visible by reading the case, so the check has to be mechanical.

The cheap one, which found four of the defects above: for each case, diff the
artifacts of its two runs *before* consulting any verdict. A fixture that
leaves the artifacts it claims to perturb byte-identical, or that moves content
between slots it claims to leave alone, shows up in one column of checksums.
`by-hand/check_reuse.py` already prints the source of each reused file, so the
same column also exposes an oracle that names the wrong source.

## Summary

| # | Case | Kind | Correction |
|---|---|---|---|
| A | 6.4, 6.2, 6.3 (sampling), composed 6×5 | Oracle | Content-based resolver for topology and sampling artifacts; it must also return *no* source when content changed |
| B | 6.2, 4.3a | Oracle | Duplicate jobs resolve to the entry they duplicate; remove any implementation wart added to force them to miss |
| C | 6.2 | Fixture | Split into 6.2a (distinct conformer added, misses) and 6.2b (duplicate added, hits) |
| C | 6.3 | Fixture | Remove a middle member instead of the last; replacement, not a pair |
| C | composed 6×5 | Fixture | Edit a conformer in `hpr_ensemble.pdb` instead of the shared molecule, so survivors exist to be reordered |
| D | 6.8 | Fixture | Split into 6.8a (`.top` edit: topoaa misses, rigidbody hits) and 6.8b (`.param` edit: both miss) |
| D | 6.16 | Fixture | Split into 6.16a (inert edit: emref misses, mdref hits) and 6.16b (substantive edit: both miss) |
| E | 6.10 | Normative | Reverse to MUST-HIT; bind the `canonical-cns` pin to the policy constant **in Stage 1**; record the executable as provenance |
| F | 4.3a | Settled | First-round replicas hit, later replicas miss — the verdict the §G.3 seeding rule produces |
| G.1 | Stage 1 canonicalizer | Normative | Bind `canonical-cns` to the policy constant in Stage 1, not in Stage 3 |
| G.2 | Stage 1 flexref/emref/mdref | Schedule | Emit replicas in rounds in all three modules; pure-function test, changelog, docs |
| G.3 | Stage 1 seeding, all five `$seed` modules | Norm | Seed from `(iniseed, job input content, repeat index)`, never from schedule position; regenerate the seeded golden forms; settles §F |

Standing constraint, not a correction: `flexref`, `emref` and `mdref` are one
job shape, tested through one representative. Any change to their emission,
seeding, indexing or cache participation applies to all three — in Stage 1's
schedule, in Stage 2's cases, and in the Stage 3 diff, where a per-module
special case in the cache path breaks the same premise without turning any test
red.
