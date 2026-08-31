"""Turn a case's declared verdicts into a per-artifact source mapping.

Every case specifies a **mapping**, not a verdict word::

    B output path  ->  expected source path,  or  None (must miss)

That is strictly more informative than an executed-vs-reused signal, because
it identifies *which* source entry was used.  A name-keyed or position-keyed
cache fails it loudly, and a key collision -- a hit from the *wrong* entry --
is caught by the same check.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from .config import CACHEABLE_MODULES
from .harness import Artifact, cacheable_artifacts, content_checksum, step_folders
from .runio import step_inputs, step_outputs

#: Modules whose jobs consume exactly one model, so output *i* is produced from
#: input *i*.  This is what makes the Axis 5 oracle possible.
PER_MODEL_MODULES = frozenset(
    {"flexref", "emref", "mdref", "emscoring", "mdscoring", "cgtoaa"}
)


@dataclass(frozen=True)
class Expectation:
    """What one output of B must be."""

    relative: str
    module: str
    #: The source files this output may be a hardlink to.  Empty means the
    #: output must be recomputed.
    #:
    #: Usually there is exactly one, and then the assertion is as strong as it
    #: can be: *this* output came from *that* entry.  It holds more than one
    #: only when several sources genuinely hold the same job -- two caches
    #: that overlap and agree -- where reuse from either is correct and
    #: insisting on one would be testing an ordering the design does not
    #: promise.
    sources: tuple[Path, ...]
    #: How the expectation was derived, for the failure message.
    origin: str

    @property
    def must_miss(self) -> bool:
        return not self.sources

    @property
    def source(self) -> Path | None:
        """The single expected source, when there is only one."""
        return self.sources[0] if len(self.sources) == 1 else None

    def describe(self) -> str:
        if not self.sources:
            return "nothing (must be recomputed)"
        if len(self.sources) == 1:
            return str(self.sources[0])
        return " or ".join(str(source) for source in self.sources)


class ExpectationError(RuntimeError):
    """A case declares an expectation that cannot be resolved."""


def resolve(
    run_dir: Path,
    sources: dict[str, Path],
    spec: dict,
) -> list[Expectation]:
    """Build the expected mapping for every cacheable artifact in ``run_dir``."""
    default = spec.get("default", "hit")
    resolver = spec.get("resolver", "same-slot")
    exact: dict[str, str | None] = spec.get("paths", {}) or {}
    patterns: list[dict] = spec.get("patterns", []) or []
    module_expect: dict[str, str] = spec.get("modules", {}) or {}

    expectations: list[Expectation] = []
    for artifact in cacheable_artifacts(run_dir):
        if artifact.relative in exact:
            declared = exact[artifact.relative]
            if declared is None:
                expectations.append(
                    Expectation(artifact.relative, artifact.module, (), "declared miss")
                )
            else:
                expectations.append(
                    Expectation(
                        artifact.relative,
                        artifact.module,
                        (_named_source(declared, sources),),
                        f"declared hit from {declared}",
                    )
                )
            continue

        verdict = _verdict_for(artifact, default, patterns, module_expect)
        if verdict == "ignore":
            # Deliberately unasserted. Used where the taxonomy's boundary is
            # real but this suite cannot know on which side a particular job
            # falls without re-deriving what CNS reads -- which would make the
            # test a restatement of the implementation. Recorded, not hidden.
            continue
        if verdict == "miss":
            expectations.append(
                Expectation(artifact.relative, artifact.module, (), "declared miss")
            )
            continue

        found = _find_sources(artifact, run_dir, sources, resolver)
        if verdict == "auto":
            # The source either holds a usable entry for this job or it does
            # not, and which it is follows from content, not from the case
            # author's bookkeeping.  This is what makes Axis 5 and Axis 9
            # expressible: the expected mapping for a reordered selection, or
            # for an interrupted fixture, must be *derived from what the
            # source actually contains* rather than declared a priori.
            expectations.append(
                Expectation(
                    artifact.relative,
                    artifact.module,
                    found,
                    "auto: a source holds this job" if found else "auto: none does",
                )
            )
            continue
        if not found:
            raise ExpectationError(
                f"case declares {artifact.relative} a hit, but no source entry "
                f"could be identified for it with resolver {resolver!r}; the "
                "case is under-specified, not the implementation at fault"
            )
        expectations.append(
            Expectation(artifact.relative, artifact.module, found, f"resolver {resolver}")
        )
    return expectations


def plausible(path: Path) -> bool:
    """Whether a source artifact looks complete enough to be reusable.

    Used only by the ``auto`` verdict, and only to keep an interrupted or
    damaged fixture from being *expected* to serve a job it visibly cannot.
    A torn PDB is present on disk but has no terminating record; expecting a
    hit from it would turn correct MUST-DEGRADE behaviour into a test failure.
    """
    for candidate in (path, Path(f"{path}.gz"), Path(f"{path}.zst")):
        if not candidate.exists():
            continue
        try:
            if candidate.stat().st_size == 0:
                return False
            if candidate.suffix == ".pdb":
                tail = candidate.read_bytes()[-4096:]
                return b"END" in tail
            return True
        except OSError:
            return False
    return False


def _verdict_for(
    artifact: Artifact,
    default: str,
    patterns: list[dict],
    module_expect: dict[str, str],
) -> str:
    for rule in patterns:
        if fnmatch.fnmatch(artifact.relative, rule["match"]):
            return rule["expect"]
    if artifact.module in module_expect:
        return module_expect[artifact.module]
    return default


def _named_source(declared: str, sources: dict[str, Path]) -> Path:
    name, _, relative = declared.partition(":")
    if not relative:
        if len(sources) != 1:
            raise ExpectationError(
                f"{declared!r} does not name a source, and this case has "
                f"{len(sources)} of them"
            )
        (root,) = sources.values()
        return root / name
    if name not in sources:
        raise ExpectationError(f"{declared!r} names an unknown source {name!r}")
    return sources[name] / relative


def _find_sources(
    artifact: Artifact,
    run_dir: Path,
    sources: dict[str, Path],
    resolver: str,
) -> tuple[Path, ...]:
    if resolver == "same-slot":
        return _same_slot(artifact, sources)
    if resolver == "refine-by-input":
        if artifact.module in PER_MODEL_MODULES:
            return _by_refinement_input(artifact, run_dir, sources)
        return _same_slot(artifact, sources)
    raise ExpectationError(f"unknown resolver {resolver!r}")


def _same_slot(artifact: Artifact, sources: dict[str, Path]) -> tuple[Path, ...]:
    """The source artifact with the same module, occurrence and basename.

    The step *ordinal* is deliberately not part of the match: Axis 2 moves it,
    and a source is identified by what its step is and what the file is called
    inside it, never by where the step sits in the workflow.
    """
    basename = artifact.relative.rsplit("/", 1)[-1]
    found = []
    for root in sources.values():
        folder = _step_folder(root, artifact.module, artifact.occurrence)
        if folder is None:
            continue
        candidate = folder / basename
        if plausible(candidate):
            found.append(candidate)
    return tuple(found)


def _step_folder(run_dir: Path, module: str, occurrence: int) -> Path | None:
    seen = 0
    for _index, name, folder in step_folders(run_dir):
        if name != module:
            continue
        if seen == occurrence:
            return folder
        seen += 1
    return None


def _by_refinement_input(
    artifact: Artifact,
    run_dir: Path,
    sources: dict[str, Path],
) -> tuple[Path, ...]:
    """Identify the source job by the *content* of the model it refines.

    This is the Axis 5 oracle.  A refinement job takes one input model, so the
    model binds to the same pin whatever its rank; the job is therefore the
    same computation exactly when the model content (and everything else it
    reads) is the same.  Matching on content rather than on rank or filename
    is what lets the test distinguish a genuine hit from a hit served by the
    *wrong* entry -- which matching on the output's own content could not,
    since a wrongly-served output has the wrong entry's bytes by definition.
    """
    folder = run_dir / artifact.relative.rsplit("/", 1)[0]
    outputs = step_outputs(folder)
    inputs = step_inputs(folder)
    basename = artifact.relative.rsplit("/", 1)[-1]
    position = next(
        (index for index, model in enumerate(outputs) if model.file_name == basename),
        None,
    )
    if position is None or position >= len(inputs):
        return ()
    wanted = content_checksum(_rebase(inputs[position].path, run_dir))

    found = []
    for root in sources.values():
        source_folder = _step_folder(root, artifact.module, artifact.occurrence)
        if source_folder is None:
            continue
        source_inputs = step_inputs(source_folder)
        source_outputs = step_outputs(source_folder)
        for index, model in enumerate(source_inputs):
            if index >= len(source_outputs):
                break
            try:
                if content_checksum(_rebase(model.path, root)) != wanted:
                    continue
            except FileNotFoundError:
                continue
            candidate = source_folder / source_outputs[index].file_name
            if plausible(candidate):
                found.append(candidate)
            break
    return tuple(found)


def _rebase(recorded: Path, run_dir: Path) -> Path:
    """Re-root a path ``io.json`` recorded, onto the run it was found in.

    ``io.json`` stores absolute paths.  A fixture that was *copied* -- and the
    corpus copies several -- therefore records paths pointing back at the run
    it was copied from, which may since have been damaged or removed.  The
    step folder and file name are what identify the file; the prefix is a
    locator, exactly as everywhere else in this suite.
    """
    if recorded.is_absolute() and not recorded.exists():
        rebased = Path(run_dir) / recorded.parent.name / recorded.name
        if rebased.exists():
            return rebased
    return recorded


def summarize(expectations: list[Expectation]) -> tuple[dict[str, int], int]:
    """``(misses per module, number of hits)`` -- the Gate 2 bound's inputs."""
    misses: dict[str, int] = {}
    hits = 0
    for expectation in expectations:
        if expectation.must_miss:
            misses[expectation.module] = misses.get(expectation.module, 0) + 1
        else:
            hits += 1
    return misses, hits


def assert_cacheable_modules_present(run_dir: Path) -> None:
    """Fail loudly if a run produced no cacheable job at all."""
    if not any(
        module in CACHEABLE_MODULES for _index, module, _folder in step_folders(run_dir)
    ):
        raise ExpectationError(f"{run_dir} contains no cacheable CNS step")
