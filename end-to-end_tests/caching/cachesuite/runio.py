"""Read HADDOCK3's public per-step ``io.json``.

This is the only HADDOCK3-internal format the suite reads, and it is
deliberately not part of the caching implementation: ``io.json`` is the
documented data-flow record between modules, and it is what
``haddock3-traceback`` consumes.  The cache's own bookkeeping -- ``CACHE`` and
``CNS_DEPENDENCIES`` -- is never read, because it is the thing under test.

``io.json`` is jsonpickle output.  It is walked as plain JSON rather than
deserialized, so the suite does not depend on the ``haddock`` package it is
testing being importable at the same version.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

_PDBFILE = "haddock.libs.libontology.PDBFile"


@dataclass(frozen=True)
class ModelRef:
    """One ``PDBFile`` entry as recorded in a step's ``io.json``."""

    file_name: str
    directory: str
    psf_name: str | None
    psf_directory: str | None
    ori_name: str | None
    score: float | None
    seed: int | None

    @property
    def path(self) -> Path:
        return Path(self.directory) / self.file_name


def _walk(node: Any) -> Iterator[dict]:
    """Yield every ``PDBFile`` dict, in document order."""
    if isinstance(node, dict):
        if node.get("py/object") == _PDBFILE:
            yield node
            return
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _as_ref(node: dict) -> ModelRef:
    topology = node.get("topology")
    psf_name = psf_directory = None
    if isinstance(topology, dict):
        psf_name = topology.get("file_name")
        psf_directory = topology.get("path")
    score = node.get("score")
    if isinstance(score, str) or score != score:  # NaN or a jsonpickle marker
        score = None
    return ModelRef(
        file_name=node.get("file_name", ""),
        directory=node.get("path", ""),
        psf_name=psf_name,
        psf_directory=psf_directory,
        ori_name=node.get("ori_name"),
        score=score,
        seed=node.get("seed"),
    )


def _read(step_dir: Path, key: str) -> list[ModelRef]:
    path = Path(step_dir) / "io.json"
    if not path.is_file():
        return []
    # ``io.json`` may contain bare NaN, which strict JSON forbids but the
    # standard library accepts.
    document = json.loads(path.read_text(encoding="utf-8"))
    return [_as_ref(node) for node in _walk(document.get(key, []))]


def step_outputs(step_dir: Path) -> list[ModelRef]:
    """Models this step produced, in the order it recorded them."""
    return _read(step_dir, "output")


def step_inputs(step_dir: Path) -> list[ModelRef]:
    """Models this step consumed, in the order it recorded them."""
    return _read(step_dir, "input")
