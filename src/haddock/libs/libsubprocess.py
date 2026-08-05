"""Run subprocess jobs."""

import os
import shlex
import shutil
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Iterable

from haddock import log
from haddock.core.defaults import cns_exec as global_cns_exec
from haddock.core.exceptions import (
    CNSRunningError,
    JobRunningError,
)
from haddock.core.typing import Any, FilePath, Optional, ParamDict
from haddock.gear.known_cns_errors import KNOWN_ERRORS as KNOWN_CNS_ERRORS
from haddock.libs.libcnsoutput import normalize_cns_pdb
from haddock.libs.libio import gzip_files
from haddock.libs.libseamless import (
    ensure_seamless_dependency_sidecars,
    make_seamless_wrapper,
    scan_cns_dependencies,
    stage_cns_job,
    write_input_manifest,
)


class BaseJob:
    """Base class for a subprocess job."""

    def __init__(
        self, input_: Path, output: Path, executable: Path, *args: Any
    ) -> None:
        self.input = input_
        self.output = output
        self.executable = executable
        self.args = args
        self.cmd: str

    def run(self) -> bytes:
        """Execute job in subprocess."""
        self.make_cmd()

        with open(self.output, "w") as outf:
            p = subprocess.Popen(
                shlex.split(self.cmd),
                stdout=outf,
                close_fds=True,
            )
            out, error = p.communicate()

        p.kill()

        if error:
            raise JobRunningError(error)

        return out

    def make_cmd(self) -> None:
        """Execute subprocess job."""
        raise NotImplementedError()


class Job(BaseJob):
    """
    Instantiate a standard job.

    Runs with the following scheme:

        $ cmd ARGS INPUT
    """

    def make_cmd(self) -> None:
        """Execute subprocess job."""
        self.cmd = " ".join(
            [
                os.fspath(self.executable),
                "".join(map(str, self.args)),  # empty string if no args
                os.fspath(self.input),
            ]
        )
        return


class JobInputFirst(BaseJob):
    """
    Instantiate a subprocess job with inverted args and input.

    Runs with the following scheme, INPUT comes first:

        $ cmd INPUT ARGS
    """

    def make_cmd(self) -> None:
        """Execute job in subprocess."""
        self.cmd = " ".join(
            [
                os.fspath(self.executable),
                os.fspath(self.input),
                "".join(map(str, self.args)),  # empty string if no args
            ]
        )
        return


class CNSJob:
    """A CNS job script."""

    def __init__(
        self,
        input_file: FilePath,
        output_file: Optional[FilePath] = None,
        error_file: Optional[FilePath] = None,
        envvars: Optional[ParamDict] = None,
        cns_exec: Optional[FilePath] = None,
        output_files: Optional[Iterable[FilePath]] = None,
        output_pdb_files: Optional[Iterable[FilePath]] = None,
        normalize_output_pdb: bool = True,
    ) -> None:
        """
        CNS subprocess.

        To execute the job, call the `.run()` method.

        Parameters
        ----------
        input_file : str or pathlib.Path
            The path to the .inp CNS file.

        output_file : str or pathlib.Path
            The path to the .out CNS file, where the standard output
            will be saved.

        envvars : dict
            A dictionary containing the environment variables needed for
            the CNSJob. These will be passed to subprocess.Popen.env
            argument.
        output_pdb_files : iterable
            PDB files expected from this CNS job. When provided, run-volatile
            CNS header lines are removed after successful execution.
        output_files : iterable
            Files expected from this CNS job. In seamless mode these files are
            copied back from the staged execution directory.
        normalize_output_pdb : bool
            Remove run-volatile CNS REMARK lines from expected output PDB files.
        """
        self.input_file = input_file
        self.output_file = output_file
        self.error_file = error_file
        self.envvars = envvars
        self.cns_exec = cns_exec
        self.output_pdb_files = [
            Path(output_pdb_file)
            for output_pdb_file in output_pdb_files or []
        ]
        self.output_files = [
            Path(output_file)
            for output_file in output_files or []
        ]
        for output_pdb_file in self.output_pdb_files:
            if output_pdb_file not in self.output_files:
                self.output_files.append(output_pdb_file)
        self.normalize_output_pdb = normalize_output_pdb
        self.execution_mode = "local"

    def __repr__(self) -> str:
        _input_file = self.input_file
        if isinstance(self.input_file, str):
            _input_file = "IO Stream"
        return (
            f"CNSJob({_input_file}, {self.output_file}, "
            f"envvars={self.envvars}, cns_exec={self.cns_exec})"
        )

    def __str__(self) -> str:
        return repr(self)

    @property
    def envvars(self) -> ParamDict:
        """CNS environment vars."""
        return self._envvars

    @envvars.setter
    def envvars(self, envvars: Optional[ParamDict]) -> None:
        """CNS environment vars."""
        self._envvars = envvars or {}
        if not isinstance(self._envvars, dict):
            raise ValueError("`envvars` must be a dictionary.")

        for k, v in self._envvars.items():
            if isinstance(v, Path):
                self._envvars[k] = str(v)

    @property
    def cns_exec(self) -> FilePath:
        """CNS executable path."""
        return self._cns_exec

    @cns_exec.setter
    def cns_exec(self, cns_exec_path: Optional[FilePath]) -> None:
        if not cns_exec_path:
            cns_exec_path = global_cns_exec  # global cns_exec

        if not os.access(cns_exec_path, mode=os.X_OK):
            raise ValueError(
                f"{str(cns_exec_path)!r} binary file not found, or is not executable."
            )

        self._cns_exec = cns_exec_path

    def run(
        self,
        compress_inp: bool = False,
        compress_out: bool = True,
        compress_seed: bool = False,
        compress_err: bool = True,
    ) -> bytes:
        """
        Run this CNS job script.

        Parameters
        ----------
        compress_inp : bool
            Compress the ``*.inp`` file to '.gz' after the run. Defaults to
            ``False``.

        compress_out : bool
            Compress the ``*.out`` file to '.gz' after the run. Defaults to
            ``True``.

        compress_seed : bool
            Compress the ``*.seed`` file to '.gz' after the run. Defaults to
            ``False``.
        """

        if self.execution_mode == "seamless":
            return self._run_with_seamless(
                compress_inp=compress_inp,
                compress_out=compress_out,
                compress_seed=compress_seed,
                compress_err=compress_err,
            )

        out = b""
        error = b""

        if isinstance(self.input_file, str):
            p = subprocess.Popen(
                self.cns_exec,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                env=self.envvars,
            )
            out, error = p.communicate(input=self.input_file.encode())
            p.kill()

        elif isinstance(self.input_file, Path) and self.output_file is not None:
            with open(self.input_file) as inp:
                p = subprocess.Popen(
                    self.cns_exec,
                    stdin=inp,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                    env=self.envvars,
                )
                out, error = p.communicate()
                p.kill()
                # Write out file
                with open(self.output_file, "wb+") as outf:
                    outf.write(out)

            if compress_inp:
                gzip_files(self.input_file, remove_original=True)

            if compress_out:
                gzip_files(self.output_file, remove_original=True)

            if compress_seed:
                with suppress(FileNotFoundError):
                    gzip_files(
                        Path(Path(self.output_file).stem).with_suffix(".seed"),
                        remove_original=True,
                    )

        # If undetected error or detect an error in the STDOUT
        if error or self.contains_cns_stdout_error(out):
            error_path = Path(self.error_file) if self.error_file is not None else None
            # Write .err file
            if error_path is not None:
                with open(error_path, "wb+") as errf:
                    errf.write(out)
            # Compress it
            if compress_err and error_path is not None:
                gzip_files(error_path, remove_original=True)
            if error:
                raise CNSRunningError(error)

        if self.normalize_output_pdb:
            self.normalize_output_pdbs()

        # Return STDOUT
        return out

    def _run_with_seamless(
        self,
        compress_inp: bool,
        compress_out: bool,
        compress_seed: bool,
        compress_err: bool,
    ) -> bytes:
        if isinstance(self.input_file, str) or self.output_file is None:
            raise CNSRunningError(
                "mode='seamless' requires CNS jobs with file-based input and output paths."
            )

        seamless_run = shutil.which("seamless-run")
        if seamless_run is None:
            raise CNSRunningError(
                "mode='seamless' requires the seamless-run executable from the seamless-suite package."
            )

        dependency_scan = scan_cns_dependencies(Path(self.input_file), self.envvars)
        if dependency_scan.unresolved_reads:
            log.debug(
                "Ignoring nonexistent CNS dependency candidates for this job: %s",
                ", ".join(dependency_scan.unresolved_reads),
            )

        input_path = Path(self.input_file)
        stable_dependency_sidecars = ensure_seamless_dependency_sidecars(
            [
                dependency
                for dependency in dependency_scan.read_files
                if dependency.resolve() != input_path.resolve()
            ]
        )

        staged = stage_cns_job(
            input_file=Path(self.input_file),
            envvars=self.envvars,
            cns_exec=Path(self.cns_exec),
            read_files=dependency_scan.read_files,
            checksum_sidecars=stable_dependency_sidecars,
        )
        wrapper = make_seamless_wrapper(staged.stage_dir)
        manifest = write_input_manifest(staged.stage_dir)

        output_path = Path(self.output_file).resolve()
        output_rel = staged.staged_path(output_path).relative_to(staged.stage_dir)
        stderr_rel = staged.staged_job_dir.relative_to(staged.stage_dir) / "stderr.txt"
        exitcode_rel = staged.staged_job_dir.relative_to(staged.stage_dir) / "exitcode.txt"

        stderr_target = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".stderr").name)
        exitcode_target = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".exitcode").name)

        job_dir_rel = staged.staged_job_dir.relative_to(staged.stage_dir)
        cns_exec_rel = os.path.relpath(staged.staged_cns_exec, staged.staged_job_dir)
        input_file_rel = os.path.relpath(staged.staged_input_file, staged.staged_job_dir)
        module_dir_rel = os.path.relpath(staged.staged_module_dir, staged.staged_job_dir)
        toppar_dir_rel = os.path.relpath(staged.staged_toppar_dir, staged.staged_job_dir)

        command = [
            seamless_run,
            "-y",
            "-g1",
            "-w",
            str(staged.stage_dir),
            "--local",
            "--input-file",
            str(manifest),
            "--metavar",
            f"JOB_DIR={job_dir_rel}",
            "--metavar",
            f"CNS_EXEC={cns_exec_rel}",
            "--metavar",
            f"INPUT_FILE={input_file_rel}",
            "--metavar",
            f"STDOUT_FILE={Path(self.output_file).name}",
            "--metavar",
            f"STDERR_FILE={Path(stderr_rel).name}",
            "--metavar",
            f"EXITCODE_FILE={Path(exitcode_rel).name}",
            "--metavar",
            f"MODULE_DIR={module_dir_rel}",
            "--metavar",
            f"TOPPAR_DIR={toppar_dir_rel}",
            "-cp",
            f"{output_rel}:{output_path}",
            "-cp",
            f"{stderr_rel}:{stderr_target}",
            "-cp",
            f"{exitcode_rel}:{exitcode_target}",
        ]

        for output_file in self.output_files:
            output_path = output_file.resolve()
            output_rel = staged.staged_path(output_path).relative_to(staged.stage_dir)
            command.extend(["-cp", f"{output_rel}:{output_path}"])

        command.extend(
            [
                str(wrapper),
            ]
        )

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise CNSRunningError(completed.stderr.encode("utf-8"))

            out = Path(self.output_file).read_bytes()
            error = stderr_target.read_bytes() if stderr_target.exists() else b""
            exit_code = 0
            if exitcode_target.exists():
                exit_code = int(exitcode_target.read_text(encoding="utf-8").strip() or "0")

            if error or self.contains_cns_stdout_error(out):
                error_path = Path(self.error_file) if self.error_file is not None else None
                if error_path is not None:
                    with open(error_path, "wb+") as errf:
                        errf.write(out)
                    if compress_err:
                        gzip_files(error_path, remove_original=True)
            if error or exit_code != 0:
                raise CNSRunningError(error or f"CNS exited with code {exit_code}".encode("utf-8"))

            if compress_inp:
                gzip_files(self.input_file, remove_original=True)

            if compress_out:
                gzip_files(self.output_file, remove_original=True)

            if compress_seed:
                with suppress(FileNotFoundError):
                    gzip_files(
                        Path(Path(self.output_file).stem).with_suffix(".seed"),
                        remove_original=True,
                    )

            if self.normalize_output_pdb:
                self.normalize_output_pdbs()

            return out
        finally:
            stderr_target.unlink(missing_ok=True)
            exitcode_target.unlink(missing_ok=True)
            shutil.rmtree(staged.stage_dir, ignore_errors=True)

    def normalize_output_pdbs(self) -> None:
        """Normalize known CNS-generated PDB outputs."""
        for output_pdb_file in self.output_pdb_files:
            normalize_cns_pdb(output_pdb_file)

    @staticmethod
    def contains_cns_stdout_error(out: bytes) -> bool:
        # Decode end of STDOUT
        # Search in last 24000 characters (300 lines * 80 characters)
        sout = out[-24000:].split(bytes(os.linesep, "utf-8"))
        # Reverse loop on lines (read backward)
        for bytes_line in reversed(sout):
            line = bytes_line.decode("utf-8")
            # This checks for an unknown CNS error
            # triggered when CNS is about to crash due to internal error
            if "^^^^^" in line:
                return True
            # Check if a known error is found
            elif any([error in line for error in KNOWN_CNS_ERRORS.keys()]):
                return True
        return False
