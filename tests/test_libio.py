"""Test libio."""
import gzip
import tempfile
from pathlib import Path
import shutil

import pytest

from haddock.libs.libio import (
    clean_suffix,
    dot_suffix,
    extract_files_flat,
    file_exists,
    folder_exists,
    gzip_files,
    read_from_yaml,
    write_dic_to_file,
    write_nested_dic_to_file,
    )

from . import emptycfg, haddock3_yaml_cfg_examples
from . import golden_data


def test_gzip_files_preserves_identical_existing_gzip(tmp_path):
    plain = tmp_path / "model.pdb"
    compressed = tmp_path / "model.pdb.gz"
    plain.write_bytes(b"PDB\n")
    with gzip.open(compressed, "wb") as output:
        output.write(plain.read_bytes())
    original_inode = compressed.stat().st_ino

    gzip_files(plain)

    assert compressed.stat().st_ino == original_inode
    with gzip.open(compressed, "rb") as restored:
        assert restored.read() == plain.read_bytes()


def test_gzip_files_atomically_replaces_different_hardlink(tmp_path):
    source = tmp_path / "source.pdb.gz"
    plain = tmp_path / "model.pdb"
    compressed = tmp_path / "model.pdb.gz"
    with gzip.open(source, "wb") as output:
        output.write(b"old\n")
    compressed.hardlink_to(source)
    source_bytes = source.read_bytes()
    source_inode = source.stat().st_ino
    plain.write_bytes(b"new\n")

    gzip_files(plain)

    assert source.read_bytes() == source_bytes
    assert source.stat().st_ino == source_inode
    assert compressed.stat().st_ino != source_inode
    with gzip.open(compressed, "rb") as restored:
        assert restored.read() == b"new\n"


@pytest.mark.parametrize(
    "cfg",
    [
        emptycfg,
        haddock3_yaml_cfg_examples,
        ],
    )
def test_read_from_yaml(cfg):
    """Test read from yaml file."""
    result = read_from_yaml(cfg)
    assert isinstance(result, dict)


def test_write_nested_dic_to_file():
    """Test write nested dictionary to file."""
    f = tempfile.NamedTemporaryFile(delete=False)
    write_nested_dic_to_file(
        data_dict={1: {"something": "something"}},
        output_fname=f.name)

    assert Path(f.name).exists()
    assert Path(f.name).stat().st_size != 0

    Path(f.name).unlink()


def test_write_dic_to_file():
    """Test write dictionary to file."""
    f = tempfile.NamedTemporaryFile(delete=False)
    write_dic_to_file(
        data_dict={"something": "something"},
        output_fname=f.name)

    assert Path(f.name).exists()
    assert Path(f.name).stat().st_size != 0

    Path(f.name).unlink()


@pytest.mark.parametrize(
    "in_,expected",
    [
        (".ext", ".ext"),
        ("ext", ".ext"),
        (".out.gz", ".out.gz"),
        ("out.gz", ".out.gz"),
        ]
    )
def test_dot_suffix(in_, expected):
    result = dot_suffix(in_)
    assert result == expected


@pytest.mark.parametrize(
    "in_,expected",
    [
        (".ext", "ext"),
        ("ext", "ext"),
        (".out.gz", "out.gz"),
        ("out.gz", "out.gz"),
        ]
    )
def test_clean_suffix(in_, expected):
    result = clean_suffix(in_)
    assert result == expected


@pytest.mark.parametrize(
    'i,expected',
    [
        (Path(__file__), Path(__file__)),
        (str(Path(__file__)), Path(__file__)),
        ],
    )
def test_file_exists(i, expected):
    """."""
    r = file_exists(i)
    assert r == expected


@pytest.mark.parametrize(
    'i',
    [
        'some_bad_path',
        Path(__file__).parent,  # this is a folder
        ],
    )
def test_file_exists_wrong(i):
    """."""
    with pytest.raises(ValueError):
        file_exists(i)


def test_folder_exists():
    """."""
    r = folder_exists(Path(__file__).parent)
    assert r == Path(__file__).parent


@pytest.mark.parametrize(
    'i',
    [
        'some_bad_path',
        Path(__file__),  # this is a file
        str(Path(__file__)),  # this is a file
        ],
    )
def test_folder_exists_wrong(i):
    """."""
    with pytest.raises(ValueError):
        folder_exists(i)


def test_folder_exists_wrong_othererror():
    with pytest.raises(TypeError):
        folder_exists("some_bad_path", exception=TypeError)


def test_extract_files_flat(monkeypatch):
    """Test extract_files_flat."""
    with tempfile.TemporaryDirectory() as tempdir:
        reference_archive = Path(golden_data, "ambig.tbl.tgz")
        shutil.copy(reference_archive, tempdir)
        monkeypatch.chdir(tempdir)
        archive = Path(reference_archive.name)
        # extract the archive
        extract_files_flat(archive, ".")
        assert Path("ambig_1.tbl").exists()
