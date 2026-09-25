import asyncio
import sys
from pathlib import Path
from typing import Callable

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices


class _AdvisoryConnection:
    def __init__(self, pool: "_AdvisoryPool") -> None:
        self.pool = pool

    async def fetchval(self, query: str, namespace: int, slot: int) -> bool:
        key = (namespace, slot)
        if "pg_try_advisory_lock" in query:
            self.pool.try_count += 1
            if key in self.pool.locks:
                return False
            self.pool.locks[key] = self
            self.pool.events.append(("lock", slot))
            return True
        if "pg_advisory_unlock" in query:
            if self.pool.locks.get(key) is not self:
                return False
            del self.pool.locks[key]
            self.pool.events.append(("unlock", slot))
            return True
        raise AssertionError(f"unexpected advisory-lock query: {query}")


class _AdvisoryPool:
    def __init__(self) -> None:
        self.locks: dict[tuple[int, int], _AdvisoryConnection] = {}
        self.acquire_count = 0
        self.release_count = 0
        self.try_count = 0
        self.events: list[tuple[str, int]] = []

    async def acquire(self, *, timeout: float) -> _AdvisoryConnection:
        del timeout
        self.acquire_count += 1
        return _AdvisoryConnection(self)

    async def release(self, connection: _AdvisoryConnection) -> None:
        assert connection not in self.locks.values()
        self.release_count += 1


class _ControlledProcess:
    def __init__(
        self,
        on_exit: Callable[[], None],
        *,
        final_returncode: int = 0,
        stderr: str = "",
        initially_finished: bool = False,
    ) -> None:
        self.returncode: int | None = None
        self._final_returncode = final_returncode
        self._stderr = stderr.encode("utf-8")
        self._finished = asyncio.Event()
        self._on_exit = on_exit
        self._reported_exit = False
        self.terminated = False
        if initially_finished:
            self._finished.set()

    async def wait(self) -> int:
        await self._finished.wait()
        if self.returncode is None:
            self.returncode = self._final_returncode
        if not self._reported_exit:
            self._reported_exit = True
            self._on_exit()
        return self.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        await self.wait()
        return b"", self._stderr

    def finish(self) -> None:
        self._finished.set()

    def terminate(self) -> None:
        self.terminated = True
        self._final_returncode = -15
        self._finished.set()

    def kill(self) -> None:
        self._final_returncode = -9
        self._finished.set()


class _ProcessFactory:
    def __init__(self, outcomes: dict[str, tuple[int, str]] | None = None, *, finish_immediately: bool = False) -> None:
        self.outcomes = outcomes or {}
        self.finish_immediately = finish_immediately
        self.processes: list[_ControlledProcess] = []
        self.started_paths: list[str] = []
        self.active = 0
        self.max_active = 0

    async def start(self, *args, **kwargs) -> _ControlledProcess:
        del args
        bundle_path = str(kwargs["cwd"])
        returncode, stderr = self.outcomes.get(bundle_path, (0, ""))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        process = _ControlledProcess(
            self._process_exited,
            final_returncode=returncode,
            stderr=stderr,
            initially_finished=self.finish_immediately,
        )
        self.processes.append(process)
        self.started_paths.append(bundle_path)
        return process

    def _process_exited(self) -> None:
        self.active -= 1


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def _bundle(tmp_path: Path, name: str, *, system_command: bool = False) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "requirements.txt").write_text("httpx==0.28.0\n", encoding="utf-8")
    if system_command:
        (root / "bundle.toml").write_text(
            "name='x'\nversion='0.1.0'\nscore_pass_threshold=0.8\n[runtime]\nsystem_dependency_commands=['echo ready']\n",
            encoding="utf-8",
        )
    return root


def _services(pool: _AdvisoryPool, *, max_concurrency: int = 8) -> AppServices:
    services = AppServices(
        Settings(
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer",
            bundle_install_max_concurrency=max_concurrency,
        )
    )
    services.postgres_pool = pool
    return services


def test_global_bundle_install_limit_blocks_ninth_instance_until_a_slot_is_released(tmp_path, monkeypatch):
    async def scenario() -> None:
        pool = _AdvisoryPool()
        factory = _ProcessFactory()
        monkeypatch.setattr("app.services._BUNDLE_INSTALL_SLOT_POLL_SECONDS", 0.01)
        monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", factory.start)
        bundles = [_bundle(tmp_path, f"bundle-{index}") for index in range(9)]
        tasks = [
            asyncio.create_task(_services(pool).ensure_bundle_requirements_installed(str(bundle)))
            for bundle in bundles
        ]

        await _wait_until(lambda: len(factory.processes) == 8)
        await asyncio.sleep(0.05)
        assert len(factory.processes) == 8
        assert len(pool.locks) == 8
        assert factory.max_active == 8

        factory.processes[0].finish()
        await _wait_until(lambda: len(factory.processes) == 9)
        assert factory.max_active == 8

        for process in factory.processes:
            process.finish()
        await asyncio.gather(*tasks)
        assert pool.locks == {}
        assert pool.release_count == 9

    asyncio.run(scenario())


def test_subprocess_failure_releases_slot_for_waiting_installer(tmp_path, monkeypatch):
    async def scenario() -> None:
        pool = _AdvisoryPool()
        failing_bundle = _bundle(tmp_path, "failing")
        waiting_bundle = _bundle(tmp_path, "waiting")
        factory = _ProcessFactory({str(failing_bundle): (7, "install failed")})
        monkeypatch.setattr("app.services._BUNDLE_INSTALL_SLOT_POLL_SECONDS", 0.01)
        monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", factory.start)

        failing_task = asyncio.create_task(
            _services(pool, max_concurrency=1).ensure_bundle_requirements_installed(str(failing_bundle))
        )
        await _wait_until(lambda: len(factory.processes) == 1)
        waiting_task = asyncio.create_task(
            _services(pool, max_concurrency=1).ensure_bundle_requirements_installed(str(waiting_bundle))
        )
        await asyncio.sleep(0.05)
        assert len(factory.processes) == 1

        factory.processes[0].finish()
        with pytest.raises(RuntimeError, match="install failed"):
            await failing_task
        await _wait_until(lambda: len(factory.processes) == 2)
        factory.processes[1].finish()
        await waiting_task
        assert pool.locks == {}

    asyncio.run(scenario())


def test_task_cancellation_stops_subprocess_and_releases_slot(tmp_path, monkeypatch):
    async def scenario() -> None:
        pool = _AdvisoryPool()
        first_bundle = _bundle(tmp_path, "cancelled")
        waiting_bundle = _bundle(tmp_path, "waiting")
        factory = _ProcessFactory()
        monkeypatch.setattr("app.services._BUNDLE_INSTALL_SLOT_POLL_SECONDS", 0.01)
        monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", factory.start)

        cancelled_task = asyncio.create_task(
            _services(pool, max_concurrency=1).ensure_bundle_requirements_installed(str(first_bundle))
        )
        await _wait_until(lambda: len(factory.processes) == 1)
        waiting_task = asyncio.create_task(
            _services(pool, max_concurrency=1).ensure_bundle_requirements_installed(str(waiting_bundle))
        )
        await asyncio.sleep(0.05)

        cancelled_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_task
        assert factory.processes[0].terminated is True
        await _wait_until(lambda: len(factory.processes) == 2)
        factory.processes[1].finish()
        await waiting_task
        assert pool.locks == {}

    asyncio.run(scenario())


def test_cancel_check_during_install_releases_slot(tmp_path, monkeypatch):
    async def scenario() -> None:
        pool = _AdvisoryPool()
        first_bundle = _bundle(tmp_path, "operator-cancelled")
        waiting_bundle = _bundle(tmp_path, "waiting")
        factory = _ProcessFactory()
        cancelled = False

        async def cancel_check() -> bool:
            return cancelled

        monkeypatch.setattr("app.services._BUNDLE_INSTALL_SLOT_POLL_SECONDS", 0.01)
        monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", factory.start)
        first_task = asyncio.create_task(
            _services(pool, max_concurrency=1).ensure_bundle_requirements_installed(
                str(first_bundle), cancel_check=cancel_check
            )
        )
        await _wait_until(lambda: len(factory.processes) == 1)
        waiting_task = asyncio.create_task(
            _services(pool, max_concurrency=1).ensure_bundle_requirements_installed(str(waiting_bundle))
        )
        await asyncio.sleep(0.05)

        cancelled = True
        with pytest.raises(RuntimeError, match="canceled by operator"):
            await first_task
        await _wait_until(lambda: len(factory.processes) == 2)
        factory.processes[1].finish()
        await waiting_task
        assert pool.locks == {}

    asyncio.run(scenario())


def test_cancel_check_while_queued_exits_without_running_command(tmp_path, monkeypatch):
    async def scenario() -> None:
        pool = _AdvisoryPool()
        active_bundle = _bundle(tmp_path, "active")
        queued_bundle = _bundle(tmp_path, "queued")
        factory = _ProcessFactory()
        queued_cancelled = False

        async def queued_cancel_check() -> bool:
            return queued_cancelled

        monkeypatch.setattr("app.services._BUNDLE_INSTALL_SLOT_POLL_SECONDS", 0.01)
        monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", factory.start)
        active_task = asyncio.create_task(
            _services(pool, max_concurrency=1).ensure_bundle_requirements_installed(str(active_bundle))
        )
        await _wait_until(lambda: len(factory.processes) == 1)
        queued_task = asyncio.create_task(
            _services(pool, max_concurrency=1).ensure_bundle_requirements_installed(
                str(queued_bundle), cancel_check=queued_cancel_check
            )
        )
        await _wait_until(lambda: pool.try_count > 1)

        queued_cancelled = True
        with pytest.raises(RuntimeError, match="canceled by operator"):
            await queued_task
        assert len(factory.processes) == 1
        assert len(pool.locks) == 1

        factory.processes[0].finish()
        await active_task
        assert pool.locks == {}

    asyncio.run(scenario())


def test_global_slot_wraps_system_commands_and_pip_sequence(tmp_path, monkeypatch):
    async def scenario() -> None:
        pool = _AdvisoryPool()
        bundle = _bundle(tmp_path, "ordered", system_command=True)
        factory = _ProcessFactory(finish_immediately=True)

        async def start_shell(*args, **kwargs):
            pool.events.append(("system-command", -1))
            return await factory.start(*args, **kwargs)

        async def start_exec(*args, **kwargs):
            pool.events.append(("pip", -1))
            return await factory.start(*args, **kwargs)

        monkeypatch.setattr("app.services.asyncio.create_subprocess_shell", start_shell)
        monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", start_exec)
        await _services(pool, max_concurrency=1).ensure_bundle_requirements_installed(str(bundle))

        assert [event[0] for event in pool.events] == ["lock", "system-command", "pip", "unlock"]

    asyncio.run(scenario())


def test_dependency_free_and_cached_fast_paths_do_not_request_another_slot(tmp_path, monkeypatch):
    async def scenario() -> None:
        pool = _AdvisoryPool()
        dependency_free = tmp_path / "dependency-free"
        dependency_free.mkdir()
        cached_bundle = _bundle(tmp_path, "cached")
        factory = _ProcessFactory(finish_immediately=True)
        monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", factory.start)
        services = _services(pool)

        await services.ensure_bundle_requirements_installed(str(dependency_free))
        assert pool.acquire_count == 0
        await services.ensure_bundle_requirements_installed(str(cached_bundle))
        assert pool.acquire_count == 1
        await services.ensure_bundle_requirements_installed(str(cached_bundle))
        assert pool.acquire_count == 1

    asyncio.run(scenario())
