"""Golden canonical forms built from real HADDOCK CNS inputs."""

import os
from copy import deepcopy
from pathlib import Path

import pytest

from haddock.libs.libcns import find_desired_linkfiles, prepare_cns_input
from haddock.libs.libontology import Format, PDBFile, Persistent
from haddock.libs.libsubprocess import CNSJob
from haddock.modules.refinement.cgtoaa import HaddockModule as Cgtoaa
from haddock.modules.refinement.emref import HaddockModule as Emref
from haddock.modules.refinement.flexref import HaddockModule as Flexref
from haddock.modules.refinement.mdref import HaddockModule as Mdref
from haddock.modules.sampling.rigidbody import HaddockModule as Rigidbody
from haddock.modules.scoring.emscoring import HaddockModule as Emscoring
from haddock.modules.scoring.mdscoring import HaddockModule as Mdscoring
from haddock.modules.topology.topoaa import HaddockModule as Topoaa
from haddock.modules.topology.topoaa import generate_topology as generate_topoaa
from haddock.modules.topology.topocg import HaddockModule as Topocg
from haddock.modules.topology.topocg import generate_topology as generate_topocg

from . import golden_data


SOURCE_GOLDEN_DATA = Path(golden_data).resolve()
GOLDEN_DIR = SOURCE_GOLDEN_DATA / "cns_canonical"
GENERIC_MODULES = {
    "rigidbody": Rigidbody,
    "flexref": Flexref,
    "emref": Emref,
    "mdref": Mdref,
    "emscoring": Emscoring,
    "mdscoring": Mdscoring,
}


@pytest.mark.parametrize(
    ("module_class", "constructed_parameters"),
    (
        (
            Rigidbody,
            {
                "int_1_2",
                "nrair_1",
                "rair_sta_1_1",
                "rair_end_1_1",
                "c6sym_seg6_1",
            },
        ),
        (
            Flexref,
            {
                "int_1_2",
                "seg_sta_1_1",
                "seg_end_1_1",
                "nseg1",
                "ncs_sta1_1",
                "c6sym_seg6_1",
            },
        ),
        (
            Emref,
            {
                "int_1_2",
                "seg_sta_1_1",
                "seg_end_1_1",
                "nseg1",
                "ncs_sta1_1",
                "c6sym_seg6_1",
            },
        ),
        (
            Mdref,
            {
                "int_1_2",
                "seg_sta_1_1",
                "seg_end_1_1",
                "nseg1",
                "ncs_sta1_1",
                "c6sym_seg6_1",
            },
        ),
        (
            Cgtoaa,
            {"int_1_2", "seg_sta_1_1", "seg_end_1_1", "nseg1"},
        ),
    ),
)
def test_constructed_cns_parameter_families_are_included(
    module_class,
    constructed_parameters,
    tmp_path,
):
    """Every affected module binds representative spliced CNS symbols."""
    module = module_class(0, tmp_path)

    assert constructed_parameters <= module.cns_params().keys()


@pytest.mark.parametrize(
    "shape",
    (
        "topoaa",
        "topocg",
        "rigidbody",
        "flexref",
        "emref",
        "mdref",
        "emscoring",
        "mdscoring",
        "cgtoaa",
    ),
)
def test_generated_cns_input_matches_canonical_golden(shape, tmp_path, monkeypatch):
    """Canonicalize a generated production input and compare its full text."""
    input_path = _stage_inputs(tmp_path)
    work_path = tmp_path / "step"
    work_path.mkdir()
    monkeypatch.chdir(work_path)
    mapping = _generated_mapping(shape, work_path, input_path)
    golden = GOLDEN_DIR / f"{shape}.inp"
    if os.environ.get("HADDOCK_UPDATE_CNS_GOLDENS") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(mapping.canonical_script, encoding="utf-8")

    assert mapping.canonical_script == golden.read_text(encoding="utf-8")


def _stage_inputs(tmp_path: Path) -> Path:
    """Create the sibling input directory used by production step paths."""
    input_path = tmp_path / "inputs"
    input_path.mkdir()
    sources = {
        name: SOURCE_GOLDEN_DATA / name
        for name in (
            "e2aP_1F3G_haddock.pdb",
            "e2aP_1F3G_haddock.psf",
            "hpr_ensemble_1_haddock.pdb",
            "hpr_ensemble_1_haddock.psf",
            "example_ambig_1.tbl",
        )
    }
    integration_data = Path(__file__).parents[1] / "integration_tests" / "golden_data"
    sources.update(
        {
            name: integration_data / name
            for name in ("e2a_haddock_cg.pdb", "e2a_haddock_cg.psf")
        }
    )
    for name, source in sources.items():
        (input_path / name).symlink_to(source.resolve())
    return input_path


def _generated_mapping(shape: str, work_path: Path, input_path: Path):
    if shape == "topoaa":
        module = Topoaa(0, work_path)
        input_pdb = input_path / "e2aP_1F3G_haddock.pdb"
        mol_params = deepcopy(module.params["mol1"])
        charged_nter = mol_params.pop("charged_nter")
        charged_cter = mol_params.pop("charged_cter")
        phosphate_5 = mol_params.pop("5_phosphate")
        mol_params.update(
            find_desired_linkfiles(
                charged_nter,
                charged_cter,
                phosphate_5,
                module.toppar_path,
            )
        )
        script = generate_topoaa(
            input_pdb,
            module.recipe_str,
            module.cns_params(),
            mol_params,
            default_params_path=module.toppar_path,
            write_to_disk=False,
        )
        output_stem = f"{input_pdb.stem}_haddock"
        outputs = [Path(f"{output_stem}.pdb"), Path(f"{output_stem}.psf")]
    elif shape == "topocg":
        module = Topocg(0, work_path)
        input_pdb = input_path / "e2aP_1F3G_haddock.pdb"
        script = generate_topocg(
            input_pdb,
            str(work_path),
            module.recipe_str,
            module.cns_params(),
            module.params["mol1"],
            default_params_path=module.toppar_path,
            write_to_disk=False,
            shape=True,
        )
        outputs = [Path(input_pdb.name), Path(f"{input_pdb.stem}.psf")]
    else:
        module, input_element, cgtoaa = _generic_input(
            shape,
            work_path,
            input_path,
        )
        script = prepare_cns_input(
            1,
            input_element,
            work_path,
            module.recipe_str,
            module.cns_params(),
            shape,
            default_params_path=module.toppar_path,
            native_segid=shape == "rigidbody",
            cgtoaa=cgtoaa,
            seed=917 if shape in {"rigidbody", "flexref", "emref", "mdref"} else None,
        )
        outputs = [Path(f"{shape}_1.pdb")]

    job = CNSJob(
        script,
        envvars=module.default_envvars(),
        output_files=outputs,
    )
    return job.canonical_mapping()


def _generic_input(shape: str, work_path: Path, input_path: Path):
    if shape == "cgtoaa":
        module = Cgtoaa(0, work_path)
        model = PDBFile(
            file_name="e2a_haddock_cg.pdb",
            path=input_path,
            topology=Persistent(
                file_name="e2a_haddock_cg.psf",
                path=input_path,
                file_type=Format.TOPOLOGY,
            ),
        )
        model.aa_topology = Persistent(
            file_name="e2aP_1F3G_haddock.psf",
            path=input_path,
            file_type=Format.TOPOLOGY,
        )
        model.cgtoaa_tbl = (input_path / "example_ambig_1.tbl").resolve()
        return module, model, True

    module = GENERIC_MODULES[shape](0, work_path)
    inputs = [
        _model("e2aP_1F3G_haddock", input_path),
        _model("hpr_ensemble_1_haddock", input_path),
    ]
    return module, inputs, False


def _model(stem: str, input_path: Path) -> PDBFile:
    return PDBFile(
        file_name=f"{stem}.pdb",
        path=input_path,
        topology=Persistent(
            file_name=f"{stem}.psf",
            path=input_path,
            file_type=Format.TOPOLOGY,
        ),
    )
