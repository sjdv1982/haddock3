"""Phase 1 CNS rigidbody witness tests."""

from pathlib import Path
import shutil

import pytest

from haddock.core.defaults import cns_exec
from haddock.gear.expandable_parameters import populate_mol_parameters_in_module
from haddock.gear.haddockmodel import HaddockModel
from haddock.libs.libio import working_directory
from haddock.libs.libontology import PDBFile, TopologyFile
from haddock.libs.libsubprocess import CNSJob
from haddock.modules.sampling.rigidbody import DEFAULT_CONFIG as DEFAULT_RIGIDBODY_CONFIG
from haddock.modules.sampling.rigidbody import HaddockModule as RigidbodyModule

from integration_tests import GOLDEN_DATA
from integration_tests.witness_helpers import (
    apply_gate_profile,
    extract_haddock_model_witnesses,
    load_baseline,
    normalize_cns_pdb_for_checksum,
)
from tests import golden_data as UNIT_GOLDEN_DATA

WITNESS_DATA = GOLDEN_DATA / "witnesses" / "rigidbody_minimization"
BASELINE = WITNESS_DATA / "baseline.yaml"
REGIMES = ("R1", "R2")
ARTIFACT_NORMALIZERS = {
    "rigidbody_1.pdb": normalize_cns_pdb_for_checksum,
}


def pytest_generate_tests(metafunc):
    if "witness_regime" not in metafunc.fixturenames:
        return
    selected_regime = metafunc.config.getoption("--witness-regime")
    regimes = REGIMES if selected_regime == "all" else [selected_regime]
    metafunc.parametrize("witness_regime", regimes)


class PreparedRigidBodyIO:
    """Provide one prepared E2A/HPR topology pair to rigidbody."""

    input_files = (
        "e2aP_1F3G_haddock.pdb",
        "e2aP_1F3G_haddock.psf",
        "hpr_ensemble_1_haddock.pdb",
        "hpr_ensemble_1_haddock.psf",
    )

    def __init__(self, path: Path) -> None:
        self.path = path

    def retrieve_models(self, crossdock: bool = False) -> list[list[PDBFile]]:
        for name in self.input_files:
            shutil.copy(UNIT_GOLDEN_DATA / name, self.path / name)
        return [
            [
                PDBFile(
                    file_name="e2aP_1F3G_haddock.pdb",
                    path=self.path,
                    topology=[
                        TopologyFile("e2aP_1F3G_haddock.psf", path=self.path),
                    ],
                ),
                PDBFile(
                    file_name="hpr_ensemble_1_haddock.pdb",
                    path=self.path,
                    topology=[
                        TopologyFile("hpr_ensemble_1_haddock.psf", path=self.path),
                    ],
                ),
            ]
        ]

    def output(self) -> None:
        return None


@pytest.mark.skipif(not cns_exec or not Path(cns_exec).exists(), reason="CNS not available")
def test_rigidbody_ajob_witness(tmp_path, witness_regime):
    """Run one generated rigidbody CNS job and compare scientific witnesses."""
    module, job = run_rigidbody_ajob(tmp_path)

    assert isinstance(job, CNSJob)
    assert module.output_models[0].seed == 918
    assert len(module.output_models) == 1

    generated_pdb = tmp_path / "rigidbody_1.pdb"
    generated_inp = tmp_path / "rigidbody_1.inp"
    generated_out = tmp_path / "rigidbody_1.out"
    assert generated_pdb.exists()
    assert generated_inp.exists()
    assert generated_out.exists()

    model = HaddockModel(generated_pdb)
    assert model.energies

    baseline = load_baseline(BASELINE)
    witnesses = extract_haddock_model_witnesses(
        generated_pdb,
        module.params,
        reference_pdb=WITNESS_DATA / "rigidbody_1.pdb",
    )
    apply_gate_profile(
        baseline,
        reference_dir=WITNESS_DATA,
        generated_dir=tmp_path,
        witnesses=witnesses,
        regime=witness_regime,
        normalizers=ARTIFACT_NORMALIZERS,
    )


def run_rigidbody_ajob(path: Path) -> tuple[RigidbodyModule, CNSJob]:
    """Generate and run the single Phase 1 rigidbody CNS job."""
    module = RigidbodyModule(
        order=0,
        path=path,
        initial_params=DEFAULT_RIGIDBODY_CONFIG,
    )
    module.params["cmrest"] = True
    module.params["sampling"] = 1
    module.params["ntrials"] = 1
    module.params["iniseed"] = 917
    module.params["debug"] = True
    module.params["ncores"] = 1
    module.params["mode"] = "local"
    module.params["ambig_fname"] = ""
    module.params["unambig_fname"] = ""
    module.params["hbond_fname"] = ""
    module.params["ranair"] = False
    module.params["surfrest"] = False
    module.params["mol_fix_origin_1"] = True
    module.params["mol_fix_origin_2"] = False
    module.previous_io = PreparedRigidBodyIO(path=path)
    populate_mol_parameters_in_module(
        module.params,
        num_mols=2,
        defaults=module._original_params,
    )

    models_to_dock = module.previous_io.retrieve_models(
        crossdock=module.params["crossdock"],
    )
    module.envvars = module.default_envvars()
    with working_directory(path):
        cns_inputs = module.prepare_cns_input_sequential(
            models_to_dock,
            sampling_factor=1,
            ambig_fnames=None,
        )
        module.output_models = []
        jobs = module.make_cns_jobs(cns_inputs)
        assert len(jobs) == 1
        jobs[0].run(compress_out=False, compress_err=False)
    return module, jobs[0]
