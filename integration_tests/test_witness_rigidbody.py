"""Phase 1 and 2 CNS rigidbody witness tests."""

from pathlib import Path
import multiprocessing as mp
import shutil

import jsonpickle
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

try:
    mp.set_start_method("fork")
except RuntimeError:
    pass

WITNESS_DATA = GOLDEN_DATA / "witnesses" / "rigidbody_minimization"
BASELINE = WITNESS_DATA / "baseline.yaml"
MODULE_WITNESS_DATA = GOLDEN_DATA / "witnesses" / "rigidbody_module"
MODULE_BASELINE = MODULE_WITNESS_DATA / "baseline.yaml"
REGIMES = ("R1", "R2")
MODULE_REGIMES = ("R2", "R3")
ARTIFACT_NORMALIZERS = {
    "rigidbody_1.pdb": normalize_cns_pdb_for_checksum,
}


def pytest_generate_tests(metafunc):
    selected_regime = metafunc.config.getoption("--witness-regime")
    if "module_witness_regime" in metafunc.fixturenames:
        if selected_regime == "all":
            regimes = MODULE_REGIMES
        elif selected_regime in MODULE_REGIMES:
            regimes = [selected_regime]
        else:
            regimes = [
                pytest.param(
                    selected_regime,
                    marks=pytest.mark.skip(reason="Phase 2 has R2/R3 gates only"),
                )
            ]
        metafunc.parametrize("module_witness_regime", regimes)
        return

    if "witness_regime" not in metafunc.fixturenames:
        return
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
        self.output = []

    def retrieve_models(self, crossdock: bool = False) -> list[list[PDBFile]]:
        self.output = []
        for name in self.input_files:
            shutil.copy(UNIT_GOLDEN_DATA / name, self.path / name)
        models_to_dock = [
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
        self.output = models_to_dock[0]
        return models_to_dock


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


@pytest.mark.skipif(not cns_exec or not Path(cns_exec).exists(), reason="CNS not available")
def test_rigidbody_module_witness(tmp_path, module_witness_regime):
    """Run the rigidbody module boundary and compare ensemble witnesses."""
    module = run_rigidbody_module(tmp_path)

    generated_pdbs = sorted(
        tmp_path.glob("rigidbody_*.pdb"),
        key=lambda path: int(path.stem.rsplit("_", 1)[1]),
    )
    assert [path.name for path in generated_pdbs] == [
        f"rigidbody_{idx}.pdb" for idx in range(1, 101)
    ]
    assert [model.seed for model in module.output_models] == list(range(918, 1018))

    io_models = load_output_models(tmp_path / "io.json")
    assert len(io_models) == 100

    parsed_rows = rigidbody_ensemble_rows(generated_pdbs, module.params)
    pdbfile_rows = pdbfile_ensemble_rows(module.output_models)
    io_rows = pdbfile_ensemble_rows(io_models)
    assert_ensemble_rows_close(pdbfile_rows, parsed_rows)
    assert_ensemble_rows_close(io_rows, parsed_rows)

    baseline = load_baseline(MODULE_BASELINE)
    witnesses = extract_rigidbody_module_witnesses(
        generated_pdbs,
        module.params,
        MODULE_WITNESS_DATA / baseline["rmsd_reference"],
    )
    apply_gate_profile(
        baseline,
        reference_dir=MODULE_WITNESS_DATA,
        generated_dir=tmp_path,
        witnesses=witnesses,
        regime=module_witness_regime,
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
    module.params["ncores"] = 10
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


def run_rigidbody_module(path: Path) -> RigidbodyModule:
    """Run the shared Phase 2 rigidbody module fixture."""
    module = RigidbodyModule(
        order=0,
        path=path,
        initial_params=DEFAULT_RIGIDBODY_CONFIG,
    )
    configure_rigidbody_pilot(module, sampling=100)
    module.previous_io = PreparedRigidBodyIO(path=path)
    module.run()
    return module


def configure_rigidbody_pilot(module: RigidbodyModule, sampling: int) -> None:
    """Apply the deterministic E2A/HPR pilot parameters to rigidbody."""
    module.params["cmrest"] = True
    module.params["sampling"] = sampling
    module.params["ntrials"] = 1
    module.params["iniseed"] = 917
    module.params["debug"] = True
    module.params["ncores"] = 10
    module.params["mode"] = "local"
    module.params["ambig_fname"] = ""
    module.params["unambig_fname"] = ""
    module.params["hbond_fname"] = ""
    module.params["ranair"] = False
    module.params["surfrest"] = False
    module.params["mol_fix_origin_1"] = True
    module.params["mol_fix_origin_2"] = False
    populate_mol_parameters_in_module(
        module.params,
        num_mols=2,
        defaults=module._original_params,
    )


def load_output_models(io_json: Path) -> list[PDBFile]:
    """Load serialized module output PDBFile objects."""
    with open(io_json, encoding="utf-8") as handle:
        return jsonpickle.decode(handle.read())["output"]


def rigidbody_ensemble_rows(
    pdb_paths: list[Path],
    weights: dict[str, float],
) -> list[list[float]]:
    """Return model index, score, and unweighted energies from PDB remarks."""
    score_weights = {key: weights[key] for key in ("w_vdw", "w_elec", "w_desolv", "w_air", "w_bsa")}
    rows = []
    for pdb_path in pdb_paths:
        model = HaddockModel(pdb_path)
        rows.append(
            [
                float(pdb_path.stem.rsplit("_", 1)[1]),
                model.calc_haddock_score(**score_weights),
                model.energies["vdw"],
                model.energies["elec"],
                model.energies["desolv"],
                model.energies["air"],
                model.energies["bsa"],
            ]
        )
    return rows


def pdbfile_ensemble_rows(models: list[PDBFile]) -> list[list[float]]:
    """Return model index, score, and unweighted energies from PDBFile objects."""
    rows = []
    for model in models:
        energies = model.unw_energies
        assert energies is not None
        rows.append(
            [
                float(model.file_name.rsplit("_", 1)[1].split(".", 1)[0]),
                model.score,
                energies["vdw"],
                energies["elec"],
                energies["desolv"],
                energies["air"],
                energies["bsa"],
            ]
        )
    return rows


def assert_ensemble_rows_close(
    observed_rows: list[list[float]],
    expected_rows: list[list[float]],
) -> None:
    """Assert two ensemble tables contain the same numeric rows."""
    assert len(observed_rows) == len(expected_rows)
    for observed, expected in zip(observed_rows, expected_rows):
        assert observed == pytest.approx(expected, abs=1.0e-8)


def extract_rigidbody_module_witnesses(
    pdb_paths: list[Path],
    weights: dict[str, float],
    rmsd_reference: Path,
) -> dict[str, object]:
    """Extract Phase 2 ensemble witnesses from generated rigidbody outputs."""
    rows = rigidbody_ensemble_rows(pdb_paths, weights)
    path_by_model_index = {
        int(path.stem.rsplit("_", 1)[1]): path
        for path in pdb_paths
    }
    sorted_rows = sorted(rows, key=lambda row: (row[1], row[0]))
    scores = [row[1] for row in sorted_rows]
    model_indices = [row[0] for row in sorted_rows]
    rmsd_by_score = [
        extract_haddock_model_witnesses(
            path_by_model_index[int(row[0])],
            weights,
            reference_pdb=rmsd_reference,
        )["rmsd_to_reference"]
        for row in sorted_rows
    ]
    tenth_score = load_baseline(MODULE_BASELINE)["witnesses"]["tenth_best_score"]["expected"]
    return {
        "output_count": len(pdb_paths),
        "best_score": scores[0],
        "best_model_index": model_indices[0],
        "tenth_best_score": scores[9],
        "score_count_at_tenth_best": sum(score <= tenth_score for score in scores),
        "scores_by_rank": scores,
        "model_indices_by_rank": model_indices,
        "vdw_by_rank": [row[2] for row in sorted_rows],
        "elec_by_rank": [row[3] for row in sorted_rows],
        "desolv_by_rank": [row[4] for row in sorted_rows],
        "air_by_rank": [row[5] for row in sorted_rows],
        "bsa_by_rank": [row[6] for row in sorted_rows],
        "rmsd_to_best_reference_by_rank": rmsd_by_score,
    }
