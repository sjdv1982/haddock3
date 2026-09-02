# Post-transplant verification findings

This report records only findings from the pilot-source transplant. It does
not modify or supersede `caching-test-failure-report.md`.

## Evidence

The complete current corpus sweep, run with `conda run -n haddock pytest -q
end-to-end_tests/caching`, completed on 2026-09-02 with **104 passed, 24
skipped, and 36 failed** in 25m03s. Phase 0 independently passed 14/14 before
the sweep.

The failures are not the already-agreed Axis 6 expectations alone. In
particular, axis 0b.1 reports all four `topoaa` PDB/PSF artifacts as false
misses after swapping molecules between pins. Location-invariance, global
orchestration, downstream-selection and composition cases likewise report
false misses. Therefore this implementation does not meet the cache contract
and these failures must be fixed rather than waived.

## Coarse-grained fixture build regression

The regenerated corpus marks fixture `cg` unusable. Its base run fails in
`libseamless._assert_canonical_script` with:

```
ValueError: Canonical CNS script leaked step-folder token '/0_topoaa'
```

The failure originates while preparing `cgtoaa` cache mappings. It blocks
Axis 13.5--13.7 rather than establishing any semantic miss/hit result.
The assertion must be made compatible with valid canonical `cgtoaa` inputs
before the corpus is rebuilt and these cases can be verified.

## Scope of the regression

Besides the agreed/disregarded Axis 6.2, 6.3, 6.4, 6.8 and 6.16 outcomes, the
failed sweep contains false misses in axes 0b, 1, 3, 4, 5, 6.5--6.9, 9 and the
composed cases. These are new implementation regressions introduced by the
pilot transplant and remain actionable.
