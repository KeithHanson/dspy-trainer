from __future__ import annotations

import sys
import threading
import time
from collections import deque
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.revision_image_build_process import RevisionImageBuildProcessRunner
from app.revision_image_builder import (
    BuildCancellation,
    RevisionImageBuildCancelled,
    RevisionImageBuildResult,
    RevisionImageBuildSpec,
)


class _FakeBuildChild:
    def __init__(self, messages=()) -> None:
        self._messages = deque(messages)
        self._alive = False
        self.started = threading.Event()
        self.terminate_count = 0
        self.kill_count = 0
        self.join_count = 0
        self.closed = False

    @property
    def exitcode(self):
        return None if self._alive else 0

    def start(self):
        self._alive = True
        self.started.set()

    def receive(self, timeout_seconds):
        if self._messages:
            message = self._messages.popleft()
            if message[0] in {"result", "error"}:
                self._alive = False
            return message
        time.sleep(timeout_seconds)
        return None

    def is_alive(self):
        return self._alive

    def terminate(self):
        self.terminate_count += 1
        self._alive = False

    def kill(self):
        self.kill_count += 1
        self._alive = False

    def join(self, timeout_seconds=None):
        self.join_count += 1

    def close(self):
        self.closed = True


class _FakeBuildChildFactory:
    def __init__(self, children) -> None:
        self.children = deque(children)
        self.specs = []

    def create(self, spec, *, max_log_bytes):
        self.specs.append((spec, max_log_bytes))
        return self.children.popleft()


def _spec(build_id: str) -> RevisionImageBuildSpec:
    return RevisionImageBuildSpec(
        owner="compose-project-a",
        module_id="module-a",
        revision_id="revision-a",
        build_id=build_id,
        generation=1,
        source_commit="commit-a",
        source_snapshot_path=Path("/snapshot/revision-a"),
        source_content_digest=f"sha256:{'a' * 64}",
        base_image_id=f"sha256:{'b' * 64}",
        image_repository="dspy-trainer-module",
        platform_version="2026.09",
    )


def _ready_result() -> RevisionImageBuildResult:
    return RevisionImageBuildResult(
        status="ready",
        local_tag="dspy-trainer-module:revision-a-g1-deadbeef",
        build_digest=f"sha256:{'c' * 64}",
        dependency_digest=f"sha256:{'d' * 64}",
        image_id=f"sha256:{'e' * 64}",
        image_digest=None,
        labels={"managed": "true"},
        build_log="finished\n",
        failure_reason=None,
    )


def test_cancellation_terminates_child_discards_result_and_next_build_progresses():
    blocked_child = _FakeBuildChild()
    completed_child = _FakeBuildChild(
        messages=(("log", "building\n"), ("result", _ready_result()))
    )
    factory = _FakeBuildChildFactory((blocked_child, completed_child))
    runner = RevisionImageBuildProcessRunner(
        factory,
        poll_interval_seconds=0.005,
        termination_grace_seconds=0.01,
    )
    cancellation = BuildCancellation()
    results = []
    errors = []

    def run_cancelled_build():
        try:
            results.append(runner.build(_spec("cancelled"), cancellation))
        except RevisionImageBuildCancelled as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_cancelled_build)
    worker.start()
    assert blocked_child.started.wait(timeout=1)

    runner.cancel(cancellation)
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert blocked_child.terminate_count == 1
    assert blocked_child.kill_count == 0
    assert blocked_child.closed
    assert results == []
    assert [str(error) for error in errors] == ["revision image build was cancelled"]

    log = []
    result = runner.build(_spec("successor"), on_log=log.append)

    assert result.status == "ready"
    assert result.image_id == f"sha256:{'e' * 64}"
    assert log == ["building\n"]
    assert completed_child.started.is_set()
    assert completed_child.terminate_count == 0
    assert completed_child.closed
    assert [spec.build_id for spec, _ in factory.specs] == ["cancelled", "successor"]
