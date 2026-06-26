# Witness-centered testing coverage assessment

*This document is an assessment of the current test coverage, not a testing
policy. It focuses on scientific "witness" variables: values derived from
generated artifacts that help decide whether a run produced scientifically
acceptable results.*

## Summary

HADDOCK3 has substantial tests for workflow execution, configuration handling,
module plumbing, and selected scientific calculations. However, the current
test suite has limited coverage of witness-centered validation of generated
workflow artifacts.

The main gap is not "CAPRI is untested". The `caprieval` machinery is tested in
several useful ways. The gap is that the tests mostly do not use witness
variables as acceptance criteria for CNS-generated artifacts, complete workflow
outputs, or ensemble-level result distributions.

In particular, the suite contains checks for:

- whether workflows and modules can run;
- whether expected output files are created;
- whether CAPRI and scoring tables have expected shapes or columns;
- whether selected witness calculations return expected values on controlled
  fixtures.

The suite mostly lacks checks such as:

- a CNS-produced PDB has expected embedded energy terms or HADDOCK score values;
- an `emscoring` or `mdscoring` run produces a specific score within tolerance;
- the top N structures contain at least a threshold number of acceptable,
  medium, or high-quality CAPRI models;
- the best cluster has at least a specified witness quality;
- score, RMSD, DockQ, or CAPRI-star distributions stay within expected ranges;
- cached or reused artifacts are scientifically equivalent, or at least not
  worse, according to domain witnesses.

## Terminology

For this assessment, a "witness" is a computed scientific value that says
something about the quality or meaning of an artifact. Examples include HADDOCK
energy terms, HADDOCK score, RMSDs, DockQ, fnat, CAPRI quality classes or
stars, cluster quality, and top-N or best-cluster summary statistics.

It is useful to distinguish three levels:

1. **Witness generation checks**: can the code produce witness files such as
   `capri_ss.tsv` or `capri_clt.tsv`?
2. **Witness calculation checks**: does the witness-producing code compute
   expected scalar values for known fixture structures?
3. **Workflow-level witness acceptance checks**: did the generated artifacts
   from a workflow satisfy scientific acceptance criteria, especially
   distributional criteria over an ensemble?

The current suite is strongest at levels 1 and 2, and much weaker at level 3.

## Current coverage

### Unit tests

The unit tests contain many exact checks for small pieces of logic. In the
witness area, the strongest examples are the CAPRI calculation tests. These
validate values such as i-RMSD, l-RMSD, il-RMSD, fnat, DockQ, global RMSD,
interface contacts, and table formatting for fixture structures.

These tests are valuable, but they mostly answer the question:

> Given known input structures or mocked values, does the witness-calculation
> code compute or format the expected value?

They usually do not answer:

> Did a complete HADDOCK workflow produce a scientifically acceptable ensemble?

There are also narrow HADDOCK score checks. For example, `HaddockModel` tests
parse a static golden PDB and verify exact energy dictionaries and
`calc_haddock_score(...)` results. This validates parsing and score arithmetic
for a known PDB, not a fresh CNS-generated output.

### Integration tests

Integration tests run individual modules, often with CNS. Coverage is mixed.

Some tests are mostly smoke tests: they assert that output PDB, `.inp`, `.out`,
or `.tsv` files exist and are non-empty. This is useful for detecting broken
execution, but it is not witness-centered validation.

Some tests perform broader but still limited witness checks. For example,
`emscoring`, `mdscoring`, `flexref`, `emref`, and `mdref` tests may check that
the resulting structure has an approximate fnat against a reference, or that a
score is a float and below a coarse threshold.

The `caprieval` integration tests are stronger: they check generated
`capri_ss.tsv` and `capri_clt.tsv` contents against expected values for score,
i-RMSD, fnat, l-RMSD, DockQ, il-RMSD, RMSD, and selected energy fields. These
are real witness-value checks for controlled module inputs.

However, this is still mostly a test of the witness generator on known inputs.
It is not the same as asserting that a full docking workflow produced an
acceptable distribution of witness values.

### End-to-end tests

The end-to-end tests are mostly workflow smoke tests. They run representative
example workflows and assert that step directories and selected output files are
created. A small number of shallow content checks exist, such as verifying that
shape atoms are present in an output PDB.

These tests are useful for checking that complete workflows do not crash, but
they do not generally inspect the scientific quality of the generated final
artifacts.

### Example-run comparison helper

The `examples/compare_runs.py` helper compares CAPRI tables between two run
directories or between development and reference example runs. It can detect
missing rows, missing columns, and numeric differences with a small tolerance.

This is the closest existing mechanism to workflow-level witness regression
checking. It is useful, but it is run-to-run comparison rather than a curated
set of domain acceptance criteria. It says "these CAPRI tables changed"; it
does not directly say "this run still has enough acceptable models in the top
100" or "the best cluster remains at least medium quality".

## Specific gap: HADDOCK score from CNS-generated artifacts

The suite verifies that HADDOCK score parsing and arithmetic work for static
or mocked inputs. It also verifies that scoring modules run and produce score
tables.

The suite does not appear to contain a test that:

1. runs CNS through a HADDOCK3 scoring or refinement module;
2. parses the newly generated PDB or scoring table;
3. asserts that the embedded HADDOCK energies or HADDOCK score match
   predefined expected values within tolerance.

Current `emscoring` and `mdscoring` integration tests check broad properties
such as score column presence, float type, coarse negativity, approximate fnat,
and some per-interface relations. They do not assert exact or tolerant expected
HADDOCK score values from the CNS-produced artifacts.

## Why this matters

For ordinary development, witness-centered testing would catch regressions that
smoke tests miss: changes in CNS input generation, energy parsing, scoring
weights, clustering behavior, CAPRI ranking, or ensemble quality can all leave
behind plausible-looking files.

For a future granular execution-cache or workflow-porting effort, such as
wrapping CNS calls and heavy analysis steps in content-addressed transformations,
witnesses become even more important. The cache boundary should be defended by
scientific checks, not only by process success and file existence.

This is framework-independent cleanup. It would be useful even without any
workflow framework change, and it would make later caching, restart, or
orchestration work safer.

## Natural places to add witness-centered tests

The most direct places are:

- targeted integration tests that run small CNS-backed modules and assert
  generated PDB energy headers, HADDOCK scores, scoring TSV values, RMSDs, or
  CAPRI metrics against curated baselines;
- small end-to-end witness tests that parse final CAPRI/scoring outputs and
  assert top-N or best-cluster quality thresholds;
- pytest-accessible helpers around the existing CAPRI table comparison logic,
  possibly extended with domain-specific summaries rather than only run-to-run
  equality checks.

Exact file equality or hashes should be used sparingly for CNS PDB outputs,
because harmless formatting, compression, platform, or executable-version
differences may make such checks brittle. Numeric witnesses with explicit
tolerances are usually more appropriate.
