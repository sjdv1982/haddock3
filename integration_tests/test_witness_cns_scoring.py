"""Phase 0 CNS scoring witness tests."""

from pathlib import Path
import shutil

import pandas as pd
import pytest

from haddock.core.defaults import cns_exec
from haddock.libs.libontology import PDBFile, TopologyFile
from haddock.modules.scoring.emscoring import DEFAULT_CONFIG as DEFAULT_EMSCORING_CONFIG
from haddock.modules.scoring.emscoring import HaddockModule as EmscoringModule

from integration_tests import GOLDEN_DATA
from integration_tests.witness_helpers import (
    apply_gate_profile,
    extract_emscoring_witnesses,
    load_baseline,
    normalize_emscoring_pdb_for_checksum,
)


WITNESS_DATA = GOLDEN_DATA / "witnesses" / "cns_scoring_nemsteps0"
BASELINE = WITNESS_DATA / "baseline.yaml"
REGIMES = ("R1", "R2")
ARTIFACT_NORMALIZERS = {
    "emscoring_1.pdb": normalize_emscoring_pdb_for_checksum,
}


def pytest_generate_tests(metafunc):
    if "witness_regime" not in metafunc.fixturenames:
        return
    selected_regime = metafunc.config.getoption("--witness-regime")
    regimes = REGIMES if selected_regime == "all" else [selected_regime]
    metafunc.parametrize("witness_regime", regimes)


class PreparedScoringIO:
    """Provide one prepared PDB/PSF pair to emscoring."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def retrieve_models(self, individualize: bool = False) -> list[PDBFile]:
        basename = "ab_ag_BHL"
        shutil.copy(GOLDEN_DATA / f"{basename}.pdb", self.path / f"{basename}.pdb")
        return [
            PDBFile(
                file_name=f"{basename}.pdb",
                path=self.path,
                topology=TopologyFile(GOLDEN_DATA / f"{basename}.psf"),
            )
        ]

    def output(self) -> None:
        return None


@pytest.mark.skipif(not cns_exec or not Path(cns_exec).exists(), reason="CNS not available")
def test_emscoring_nemsteps0_witness(tmp_path, witness_regime):
    """Score fixed coordinates with CNS and compare scientific witnesses."""
    module = run_emscoring_nemsteps0(tmp_path)

    scored_pdb = tmp_path / "emscoring_1.pdb"
    score_table = tmp_path / "emscoring.tsv"
    assert scored_pdb.exists()
    assert score_table.exists()
    assert not (tmp_path / "emscoring_B_H.tsv").exists()

    score_df = pd.read_csv(score_table, sep="\t", comment="#")
    assert score_df.columns.tolist() == ["structure", "original_name", "md5", "score"]
    assert score_df.shape == (1, 4)
    assert score_df.loc[0, "structure"] == "emscoring_1.pdb"
    assert score_df.loc[0, "original_name"] == "ab_ag_BHL.pdb"
    assert score_df.loc[0, "score"] == pytest.approx(-317.970, abs=1.0e-3)

    baseline = load_baseline(BASELINE)
    witnesses = extract_emscoring_witnesses(
        input_pdb=GOLDEN_DATA / "ab_ag_BHL.pdb",
        scored_pdb=scored_pdb,
        weights=module.params,
    )
    apply_gate_profile(
        baseline,
        reference_dir=WITNESS_DATA,
        generated_dir=tmp_path,
        witnesses=witnesses,
        regime=witness_regime,
        normalizers=ARTIFACT_NORMALIZERS,
    )


def run_emscoring_nemsteps0(path: Path) -> EmscoringModule:
    """Run the shared Phase 0 emscoring fixture."""
    module = EmscoringModule(
        order=0,
        path=path,
        initial_params=DEFAULT_EMSCORING_CONFIG,
    )
    module.params["nemsteps"] = 0
    module.params["per_interface_scoring"] = False
    module.params["mode"] = "local"
    module.params["ncores"] = 1
    module.previous_io = PreparedScoringIO(path=path)
    module.run()
    return module
