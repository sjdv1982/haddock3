# Caching test incompatibility report

## Axis 6.2 — a duplicate ensemble member is required to miss

This case is incompatible with a plain content-addressed cache and is retained
as an intentional failure under the selected cache policy.

Its `add` perturbation appends the first `MODEL` block from
`hpr_ensemble.pdb` unchanged. The newly created member 11 is therefore
byte-for-byte identical to member 1. The topology CNS job has the same
canonical input and dependency checksums, and its PDB and PSF outputs are the
same artifacts. A content key correctly restores member 11 from the existing
member-1 entry.

The suite instead classifies that restore as catastrophic: it requires a
second occurrence of identical content to execute and miss the cache, while
allowing the same content to hit in other cases. Making the assertion pass
would require an occurrence/rank value to be included in the topology key.
That value is not content: it prevents valid deduplication solely because an
identical member appeared earlier in the ensemble.

I briefly implemented that occurrence discriminator to verify the diagnosis.
It made the duplicate topology job miss, but changed the key from plain
content identity to content-plus-list-position identity. It has been removed.
The implementation deliberately keeps the content-only behavior: member 11
may reuse member 1's cache entry.

## Axis 6.4 — ensemble members reordered

This case cannot simultaneously satisfy its current Gate 1 expectation and a
content-addressed cache contract.

The perturbation swaps the first two `MODEL` blocks in `hpr_ensemble.pdb`.
After HADDOCK splits that ensemble, the materialized inputs establish the
following byte-level mapping (SHA-256):

| member path | SHA-256 |
| --- | --- |
| source `hpr_ensemble_1.pdb` | `7afd60e20ad2b4dab00624cd5b96bdefc59d7bea2e1b2b6de4f2bb5be3037a8f` |
| source `hpr_ensemble_2.pdb` | `6adf628702ec73e7cb66f4f63bde9c0f0feb9ed5116957bd68d388586d0b44a5` |
| reordered target `hpr_ensemble_1.pdb` | `6adf628702ec73e7cb66f4f63bde9c0f0feb9ed5116957bd68d388586d0b44a5` |
| reordered target `hpr_ensemble_2.pdb` | `7afd60e20ad2b4dab00624cd5b96bdefc59d7bea2e1b2b6de4f2bb5be3037a8f` |

Thus a content-addressed topology key correctly maps target member 1 to source
member 2, and target member 2 to source member 1. The test's current
same-slot oracle instead requires target member 1 to hardlink source member 1
and vice versa. That would serve coordinates/topology for a different CNS
input, which is a wrong cache entry, not a hit.

The case prose says that member jobs are unchanged under reordering, which is
consistent with content-based cross-slot reuse. The Gate 1 same-slot assertion
is not. Satisfying the latter would require a name-based key or deliberately
returning wrong results, both incompatible with the requested content-based
cache.

## Axis 6.3 — removing a conformer changes rigidbody combinations

The input ensemble has ten members; the perturbation removes one, leaving
nine. Rigidbody sampling emits forty jobs by cycling through the available
combinations. Consequently, jobs 1–9 retain their inputs, but job 10 and
every subsequent job no longer refer to the same combination as the source
slot (the target cycle has returned to member 1 while the source slot 10 used
member 10).

The cache evidence reflects this: source and target job-10 keys differ
(`d5ce04…ab30c` versus `32a491…948a6`), as do job-11 keys
(`cddcfb…001c9` versus `6e7317…4aeb9`). Recomputing target jobs 10–40 is
therefore correct.

The case prose acknowledges that the sampling stage has a different set of
combinations, but `default: auto` resolves rigidbody artifacts by same slot.
It consequently requires jobs 10–40 to hardlink their old source slots.
Those source slots are different CNS inputs, so satisfying the assertion
would serve stale docking models. This cannot pass under a content-based
cache.

## Axis 6.8 — force-field changed, byte-identical descendants

The changed `protein-allhdg5-4.top` is correctly present in each affected
topology job's read-set, so those topology jobs miss. In this fixture their
new PDB/PSF artifacts are nevertheless byte-identical to the cached ones.
The following rigidbody jobs therefore receive the same canonical CNS script,
the same input-artifact bytes, and the same direct dependency checksums as
before. A plain content key restores all four rigidbody results.

The suite requires those rigidbody jobs to miss solely because an ancestor
job read a changed toppar file. Satisfying that requires producer provenance
(the identity of the job that produced an input) as an additional downstream
key component. That makes a key depend on history rather than the job's
direct content/read-set, and is explicitly disallowed. Under the selected
plain-content policy, the observed rigidbody hits are correct reuse.

## Axis 6.16 — changed emref template, byte-identical mdref inputs

This simulated upgrade appends a comment to the emref CNS template. The emref
jobs miss because their canonical scripts differ. In the tested run, however,
their emitted PDB files are byte-identical to the source outputs. The two
mdref jobs consequently have unchanged canonical scripts and unchanged direct
input/dependency content, and they correctly hit a plain content cache.

The test requires mdref to miss based only on the changed emref producer. As
with Axis 6.8, that is a transitive producer-provenance policy, not plain
content addressing. Such provenance is intentionally not implemented, so the
two reported mdref hits are expected under the chosen contract.

## Axis 6.10 — CNS executable changed

The suite deliberately changes the CNS executable to a shell wrapper and
requires every cacheable job to miss. The selected project policy is the
opposite: “which CNS does not matter.” The Seamless input pin for
`canonical-cns` is therefore deliberately constant, rather than a checksum
of the configured executable path or its bytes.

Consequently, the wrapper case hits when all job content and direct read
dependencies match. This is an optimistic reuse policy: HADDOCK cannot
reliably decide whether two CNS paths are equivalent, so executable identity
is intentionally not treated as cache content. Passing this test would
require reversing that explicit policy decision.

## Composed 6×5 — coordinate change is asserted as a same-slot hit

The case changes one coordinate in `e2aP_1F3G.pdb`. Its prose correctly says
that every job which reads the changed molecule must miss. The materialized
topology input confirms that the change reaches CNS:

| input | SHA-256 |
| --- | --- |
| source `data/0_topoaa/e2aP_1F3G.pdb` | `a05c89f3c337eb1c8d44520831efe500dbfe57b4dd8447791ef592139df9613a` |
| target `data/0_topoaa/e2aP_1F3G.pdb` | `143545dd25841876ad4d6909a4f48e4269b5d65a003ee6e5d390d6b048b91ca9` |

The corresponding topology cache keys differ (`0cf235…38eff` in the source,
`eb261f…1e0a0` in the target), as do the rigidbody keys (`886eaa…0405c` and
`b09e6b…535f` for job 1). Recomputing these jobs is therefore required by a
content key.

The test declaration uses `default: auto` with `resolver: refine-by-input`.
That resolver only applies content matching to per-model refinement modules;
for topology and rigidbody it falls back to same-slot source paths. It thus
expects the changed topology and all rigidbody outputs to hardlink their
source counterparts, contradicting the case prose and content-addressed
semantics. Passing the assertion would require serving stale results.
