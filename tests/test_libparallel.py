import uuid
import time
import threading
from multiprocessing import Queue
from pathlib import Path

import pytest

from haddock.core.exceptions import HaddockTaskExecutionError
from haddock.libs.libcache import CacheContext, CacheIndex, CacheRecord
from haddock.libs.libcnsoutput import is_normalized_cns_pdb
from haddock.libs.libparallel import (
    CacheRecordWriter,
    GenericTask,
    Scheduler,
    Worker,
    get_index_list,
    split_tasks,
)
from haddock.libs.libsubprocess import CNSJob


class Task:
    """Dummy task class to be used in the test.

    This is a simple task that receives an integer and returns the integer + 1.

    **The important part is that the task is a class that implements a `run` method.**
    """

    def __init__(self, input):
        self.input = input
        self.output = None

    def run(self) -> int:
        self.output = self.input + 1
        return self.output


class FileTask:
    """Dummy task class to be used in the test.

    This is a simple task that receives a filename and creates an empty file with that name.

    **The important part is that the task is a class that implements a `run` method.**
    """

    def __init__(self, filename):
        self.input_file = Path(filename)

    def run(self):
        Path(self.input_file).touch()


class TaskWithException:
    def __init__(self):
        pass

    def run(self):
        raise HaddockTaskExecutionError("Test error")


class TaskWithUnexpectedException:
    def run(self):
        raise ValueError("Unexpected test error")


class DelayedTask(Task):
    def __init__(self, input, delay):
        super().__init__(input)
        self.delay = delay

    def run(self) -> int:
        time.sleep(self.delay)
        return super().run()


class CacheOutputTask:
    """Cache-aware task whose records are written only by the parent thread."""

    cache_context = None
    cache_debug = False

    def __init__(self, output: Path, record: Path, fail: bool = False):
        self.output = output
        self.record = record
        self.fail = fail

    def run(self):
        if self.fail:
            raise HaddockTaskExecutionError("expected CNS failure")
        self.output.write_text("PDB\n", encoding="utf-8")
        # The writer sees the output during this sleep, but cannot record it
        # until this worker signals completion in its finally block.
        time.sleep(0.15)
        if self.record.exists():
            (self.record.parent / "recorded-too-early").touch()

    def cache_outputs_present(self) -> bool:
        return self.output.is_file()

    def write_cache_success_record(self) -> None:
        self.record.write_text("success", encoding="utf-8")

    def write_cache_failure_record(self) -> None:
        self.record.write_text("FAILED", encoding="utf-8")


class CacheCNSOutputTask(CNSJob):
    """CNS-shaped task that writes an unnormalized PDB without CNS itself."""

    def __init__(self, *args, wait_for_termination: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.wait_for_termination = wait_for_termination

    def run(self):
        output = self._absolute_output(self.output_pdb_files[0])
        output.write_text("REMARK DATE: volatile\nATOM\n", encoding="utf-8")
        if self.wait_for_termination:
            time.sleep(10)
        return b""


def _cache_cns_task(tmp_path, wait_for_termination: bool = False):
    cns_exec = tmp_path / "cns"
    cns_exec.write_text("#!/bin/sh\n", encoding="utf-8")
    cns_exec.chmod(0o755)
    module = tmp_path / "module"
    toppar = tmp_path / "toppar"
    module.mkdir()
    toppar.mkdir()
    output = tmp_path / "model.pdb"
    task = CacheCNSOutputTask(
        "stop\n",
        cns_exec=cns_exec,
        envvars={"MODULE": str(module), "TOPPAR": str(toppar)},
        output_files=[output],
        wait_for_termination=wait_for_termination,
    )
    return task, output, CacheContext(current_run=tmp_path, source_index=None)


class CachePriorityTask:
    """Minimal cache-aware task with one predeclared PDB output."""

    cache_context = None
    cache_debug = False

    def __init__(self, work_dir: Path, name: str):
        self.work_dir = work_dir
        self.name = name
        self.output_pdb_files = [Path(f"{name}.pdb")]

    def run(self):
        return None

    def cache_outputs_present(self) -> bool:
        return False

    def write_cache_success_record(self) -> None:
        return None

    def write_cache_failure_record(self) -> None:
        return None

    def has_cached_output_file(self) -> bool:
        relative = self.work_dir.relative_to(self.cache_context.current_run)
        pdb_path = (relative / self.output_pdb_files[0]).as_posix()
        return any(
            record.pdb_path == pdb_path
            for record in self.cache_context.source_index.records.values()
        )


@pytest.fixture
def worker():
    """Return a worker with 3 tasks."""
    yield Worker(tasks=[Task(1), Task(2), Task(3)], results=Queue())


@pytest.fixture
def scheduler():
    """Return a scheduler with 3 tasks."""
    yield Scheduler(
        ncores=1,
        tasks=[
            Task(1),
            Task(2),
            Task(3),
        ],
    )


@pytest.fixture
def scheduler_files():
    """Return a scheduler with 3 tasks that create files."""

    file_list = [uuid.uuid4().hex for _ in range(3)]
    yield Scheduler(
        ncores=1,
        tasks=[
            FileTask(file_list[0]),
            FileTask(file_list[1]),
            FileTask(file_list[2]),
        ],
    )

    for f in file_list:
        try:
            Path(f).unlink()
        except FileNotFoundError:
            pass


@pytest.fixture
def scheduler_with_exception():
    """Return a scheduler with 3 tasks, one of them raises an exception."""
    yield Scheduler(
        ncores=1,
        tasks=[
            Task(1),
            TaskWithException(),
            Task(3),
        ],
    )


def test_split_tasks():

    lst = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    n = 3
    result = list(split_tasks(lst, n))
    assert result == [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10]]

    n = 4
    result = list(split_tasks(lst, n))
    assert result == [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10]]


def test_split_tasks_empty():
    # An empty list must not raise (range(0, 0, 0) ValueError); yields nothing.
    assert list(split_tasks([], 3)) == []


def test_get_index_list():

    nmodels = 10
    ncores = 3
    result = get_index_list(nmodels, ncores)
    assert result == [0, 4, 7, 10]

    nmodels = 10
    ncores = 4
    result = get_index_list(nmodels, ncores)
    assert result == [0, 3, 6, 8, 10]


def test_worker_run(worker):

    _ = worker.run()

    assert worker.tasks[0].output == 2
    assert worker.tasks[1].output == 3
    assert worker.tasks[2].output == 4


def test_scheduler_files(scheduler_files):

    _ = scheduler_files.run()

    assert Path(scheduler_files.worker_list[0].tasks[0].input_file).exists()
    assert Path(scheduler_files.worker_list[0].tasks[1].input_file).exists()
    assert Path(scheduler_files.worker_list[0].tasks[2].input_file).exists()


def test_scheduler(scheduler):

    _ = scheduler.run()

    assert scheduler.results[0] == 2
    assert scheduler.results[1] == 3
    assert scheduler.results[2] == 4


def test_scheduler_preserves_task_order_when_workers_finish_out_of_order():
    scheduler = Scheduler(
        tasks=[DelayedTask(1, 0.1), DelayedTask(2, 0)],
        ncores=2,
    )

    scheduler.run()

    assert scheduler.results == [2, 3]


def test_scheduler_only_stamps_cache_aware_tasks():
    class CacheAwareTask(Task):
        cache_context = None
        cache_debug = False

    context = object()
    scheduler = Scheduler(
        tasks=[CacheAwareTask(1), Task(2)],
        ncores=1,
        cache_context=context,
        cache_debug=True,
    )

    assert scheduler.worker_list[0].tasks[0].cache_context is context
    assert scheduler.worker_list[0].tasks[0].cache_debug is True
    assert not hasattr(scheduler.worker_list[0].tasks[1], "cache_context")


def test_scheduler_cache_writer_waits_for_worker_completion(tmp_path):
    output = tmp_path / "model.pdb"
    record = tmp_path / "record"
    scheduler = Scheduler(
        tasks=[CacheOutputTask(output, record)],
        ncores=1,
        cache_context=object(),
    )

    scheduler.run()

    assert record.read_text(encoding="utf-8") == "success"
    assert not (tmp_path / "recorded-too-early").exists()
    assert scheduler.is_shutdown.is_set()
    assert not scheduler.cache_writer._thread.is_alive()


def test_scheduler_cache_writer_defers_failed_record_until_final_cycle(tmp_path):
    scheduler = Scheduler(
        tasks=[CacheOutputTask(tmp_path / "model.pdb", tmp_path / "record", fail=True)],
        ncores=1,
        cache_context=object(),
    )

    scheduler.run()

    assert not (tmp_path / "record").exists()
    scheduler.finalize_cache_records()
    assert (tmp_path / "record").read_text(encoding="utf-8") == "FAILED"


def test_scheduler_prioritizes_available_source_cache_pdbs(tmp_path):
    current = tmp_path / "current"
    source = tmp_path / "source"
    work_dir = current / "01_rigidbody"
    work_dir.mkdir(parents=True)
    cached_names = ("cached_1", "cached_2")
    records = {}
    for number, name in enumerate(cached_names):
        relative = f"01_rigidbody/{name}.pdb"
        checksum = f"{number + 1:064x}"
        records[checksum] = CacheRecord(checksum, "a" * 64, relative, "")
    context = CacheContext(current, CacheIndex(source, records))
    tasks = [
        CachePriorityTask(work_dir, "miss_1"),
        CachePriorityTask(work_dir, "cached_1"),
        CachePriorityTask(work_dir, "miss_2"),
        CachePriorityTask(work_dir, "cached_2"),
    ]

    scheduler = Scheduler(tasks, ncores=2, cache_context=context)

    assert [worker.tasks[0].name for worker in scheduler.worker_list] == list(cached_names)
    assert [task.name for task in scheduler.task_batches[1]] == ["miss_1", "miss_2"]


def test_cache_writer_exits_when_scheduler_is_marked_shutdown():
    scheduler_shutdown = threading.Event()
    writer = CacheRecordWriter([], Queue(), scheduler_shutdown)

    writer.start()
    scheduler_shutdown.set()
    writer.join_after_scheduler_shutdown()

    assert not writer._thread.is_alive()


def test_cache_writer_normalizes_before_appending_cns_record(tmp_path):
    task, output, context = _cache_cns_task(tmp_path)
    scheduler = Scheduler([task], ncores=1, cache_context=context)

    scheduler.run()

    assert is_normalized_cns_pdb(output)
    assert (tmp_path / "CACHE").is_file()


def test_scheduler_termination_does_not_append_unnormalized_cns_output(tmp_path):
    task, output, context = _cache_cns_task(tmp_path, wait_for_termination=True)
    scheduler = Scheduler([task], ncores=1, cache_context=context)
    scheduler.cache_writer.start()
    scheduler.worker_list[0].start()
    deadline = time.monotonic() + 5
    while not output.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    scheduler.terminate()
    scheduler.worker_list[0].join()

    assert output.exists()
    assert not is_normalized_cns_pdb(output)
    assert not (tmp_path / "CACHE").exists()


def test_scheduler_with_exception(scheduler_with_exception):

    _ = scheduler_with_exception.run()

    assert scheduler_with_exception.results[0] == 2
    assert scheduler_with_exception.results[1] is None
    assert scheduler_with_exception.results[2] == 4


def test_worker_propagates_unexpected_exception():
    worker = Worker(tasks=[TaskWithUnexpectedException()], results=Queue())
    with pytest.raises(ValueError, match="Unexpected test error"):
        worker.run()


def test_generic_task_init():
    def sample_function(a, b, c=3):
        return a + b + c

    # Test with only a function
    task1 = GenericTask(sample_function)
    assert task1.function == sample_function
    assert task1.args == ()
    assert task1.kwargs == {}

    # Test with function and positional arguments
    task2 = GenericTask(sample_function, 1, 2)
    assert task2.function == sample_function
    assert task2.args == (1, 2)
    assert task2.kwargs == {}

    # Test with function, positional and keyword arguments
    task3 = GenericTask(sample_function, 1, b=2, c=4)
    assert task3.function == sample_function
    assert task3.args == (1,)
    assert task3.kwargs == {"b": 2, "c": 4}

    # Test with a lambda function
    lambda_func = lambda x: x * 2
    task4 = GenericTask(lambda_func, 5)
    assert task4.function == lambda_func
    assert task4.args == (5,)
    assert task4.kwargs == {}

    # Test with built-in function
    task5 = GenericTask(len, "hello")
    assert task5.function == len
    assert task5.args == ("hello",)
    assert task5.kwargs == {}


def test_generic_task_init_exceptions():
    # Test that providing a non-callable raises a TypeError
    with pytest.raises(TypeError):
        GenericTask("not a function")


def test_generic_task_init_types():
    def dummy_function():
        pass

    task = GenericTask(dummy_function, 1, 2, a=3)
    assert callable(task.function)
    assert isinstance(task.args, tuple)
    assert isinstance(task.kwargs, dict)


import pytest


def test_generic_task_run():
    # Test with a simple function
    def add(a, b):
        return a + b

    task = GenericTask(add, 2, 3)
    assert task.run() == 5

    # Test with a function that returns None
    def do_nothing():
        pass

    task = GenericTask(do_nothing)
    assert task.run() is None

    # Test with a function that takes keyword arguments
    def greet(name, greeting="Hello"):
        return f"{greeting}, {name}!"

    task = GenericTask(greet, "Alice", greeting="Hi")
    assert task.run() == "Hi, Alice!"

    # Test with a lambda function
    task = GenericTask(lambda x: x * 2, 5)
    assert task.run() == 10

    # Test with a built-in function
    task = GenericTask(len, "hello")
    assert task.run() == 5

    # Test with a method of a class
    class TestClass:
        def method(self, x):
            return x * 2

    obj = TestClass()
    task = GenericTask(obj.method, 3)
    assert task.run() == 6


def test_generic_task_run_exceptions():
    # Test that running a task with a non-callable raises a TypeError
    with pytest.raises(TypeError):
        task = GenericTask("not a function")
        task.run()

    # Test that running a task with incorrect arguments raises a TypeError
    def func(a, b):
        return a + b

    task = GenericTask(func, 1)  # Missing one argument
    with pytest.raises(TypeError):
        task.run()

    # Test that the function's exceptions are propagated
    def raise_value_error():
        raise ValueError("Test error")

    task = GenericTask(raise_value_error)
    with pytest.raises(ValueError, match="Test error"):
        task.run()


def test_generic_task_run_with_complex_args():
    # Test with *args and **kwargs in the function
    def complex_func(*args, **kwargs):
        return sum(args) + sum(kwargs.values())

    task = GenericTask(complex_func, 1, 2, 3, a=4, b=5)
    assert task.run() == 15

    # Test with a function that modifies mutable arguments
    def modify_list(lst):
        lst.append(4)
        return lst

    original_list = [1, 2, 3]
    task = GenericTask(modify_list, original_list)
    result = task.run()
    assert result == [1, 2, 3, 4]
    assert original_list == [1, 2, 3, 4]  # The original list is modified


@pytest.mark.skip("WIP")
def test_scheduler_terminate(scheduler_files):
    pass
