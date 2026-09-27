from __future__ import annotations

import multiprocessing
import threading
from collections.abc import Callable
from typing import Any, Protocol

from app.revision_image_builder import (
    BuildCancellation,
    DockerSdkImageAdapter,
    RevisionImageBuilder,
    RevisionImageBuildError,
    RevisionImageBuildResult,
    RevisionImageBuildSpec,
)
from app.revision_images import MAX_BUILD_LOG_BYTES


class RevisionImageBuildChild(Protocol):
    @property
    def exitcode(self) -> int | None: ...

    def start(self) -> None: ...

    def receive(self, timeout_seconds: float) -> tuple[str, Any] | None: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def join(self, timeout_seconds: float | None = None) -> None: ...

    def close(self) -> None: ...


class RevisionImageBuildChildFactory(Protocol):
    def create(
        self,
        spec: RevisionImageBuildSpec,
        *,
        max_log_bytes: int,
    ) -> RevisionImageBuildChild: ...


class MultiprocessingRevisionImageBuildChildFactory:
    def __init__(self, *, start_method: str = "spawn") -> None:
        self._context = multiprocessing.get_context(start_method)

    def create(
        self,
        spec: RevisionImageBuildSpec,
        *,
        max_log_bytes: int,
    ) -> RevisionImageBuildChild:
        receiver, sender = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_revision_image_build_child_main,
            args=(spec, max_log_bytes, sender),
            name=f"revision-image-build-{spec.build_id}",
            daemon=False,
        )
        return _MultiprocessingRevisionImageBuildChild(
            process=process,
            receiver=receiver,
            sender=sender,
        )


class _MultiprocessingRevisionImageBuildChild:
    def __init__(self, *, process: Any, receiver: Any, sender: Any) -> None:
        self._process = process
        self._receiver = receiver
        self._sender = sender

    @property
    def exitcode(self) -> int | None:
        return self._process.exitcode

    def start(self) -> None:
        self._process.start()
        self._sender.close()

    def receive(self, timeout_seconds: float) -> tuple[str, Any] | None:
        if not self._receiver.poll(timeout_seconds):
            return None
        try:
            message = self._receiver.recv()
        except EOFError:
            return None
        if not isinstance(message, tuple) or len(message) != 2:
            raise RevisionImageBuildError("build child returned an invalid message")
        return message

    def is_alive(self) -> bool:
        return self._process.is_alive()

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    def join(self, timeout_seconds: float | None = None) -> None:
        self._process.join(timeout_seconds)

    def close(self) -> None:
        self._receiver.close()
        self._sender.close()
        self._process.close()


class RevisionImageBuildProcessRunner:
    """Runs each blocking Docker SDK build in one terminable child process."""

    def __init__(
        self,
        child_factory: RevisionImageBuildChildFactory,
        *,
        max_log_bytes: int = MAX_BUILD_LOG_BYTES,
        poll_interval_seconds: float = 0.05,
        termination_grace_seconds: float = 0.5,
    ) -> None:
        if not 1 <= max_log_bytes <= MAX_BUILD_LOG_BYTES:
            raise ValueError(
                f"max_log_bytes must be between 1 and {MAX_BUILD_LOG_BYTES}"
            )
        if poll_interval_seconds <= 0 or termination_grace_seconds <= 0:
            raise ValueError("process runner timing values must be positive")
        self._child_factory = child_factory
        self._max_log_bytes = max_log_bytes
        self._poll_interval_seconds = poll_interval_seconds
        self._termination_grace_seconds = termination_grace_seconds
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._active_child: RevisionImageBuildChild | None = None

    def build(
        self,
        spec: RevisionImageBuildSpec,
        cancellation: BuildCancellation | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> RevisionImageBuildResult:
        cancellation = cancellation or BuildCancellation()
        cancellation.raise_if_cancelled()
        with self._lock:
            if self._active_child is not None:
                raise RevisionImageBuildError(
                    "a revision image build child is already active"
                )
            child = self._child_factory.create(
                spec,
                max_log_bytes=self._max_log_bytes,
            )
            try:
                child.start()
            except BaseException:
                child.close()
                raise
            self._active_child = child
        try:
            while True:
                cancellation.raise_if_cancelled()
                message = child.receive(self._poll_interval_seconds)
                cancellation.raise_if_cancelled()
                if message is not None:
                    kind, payload = message
                    if kind == "log":
                        if on_log is not None:
                            on_log(str(payload))
                        continue
                    if kind == "result" and isinstance(
                        payload, RevisionImageBuildResult
                    ):
                        return payload
                    if kind == "error":
                        raise RevisionImageBuildError(
                            f"revision image build child failed: {payload}"
                        )
                    raise RevisionImageBuildError(
                        f"revision image build child returned unsupported message: {kind}"
                    )
                if not child.is_alive():
                    raise RevisionImageBuildError(
                        "revision image build child exited without a result "
                        f"(exitcode={child.exitcode})"
                    )
        finally:
            self._terminate(child)
            with self._lock:
                if self._active_child is child:
                    with self._lifecycle_lock:
                        child.close()
                    self._active_child = None

    def cancel(self, cancellation: BuildCancellation) -> None:
        cancellation.cancel()
        with self._lock:
            child = self._active_child
            if child is not None:
                self._terminate(child)

    def _terminate(self, child: RevisionImageBuildChild) -> None:
        with self._lifecycle_lock:
            if not child.is_alive():
                child.join()
                return
            child.terminate()
            child.join(self._termination_grace_seconds)
            if child.is_alive():
                child.kill()
                child.join()


def _revision_image_build_child_main(
    spec: RevisionImageBuildSpec,
    max_log_bytes: int,
    sender: Any,
) -> None:
    try:
        builder = RevisionImageBuilder(
            DockerSdkImageAdapter.from_env(),
            max_log_bytes=max_log_bytes,
        )
        result = builder.build(
            spec,
            on_log=lambda text: sender.send(("log", text)),
        )
        sender.send(("result", result))
    except BaseException as exc:  # noqa: BLE001
        sender.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        sender.close()
