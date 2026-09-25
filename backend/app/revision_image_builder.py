from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile
import tomllib
from typing import Any, BinaryIO, Iterable, Iterator, Mapping, Protocol

from app.revision_images import MAX_BUILD_LOG_BYTES, MAX_FAILURE_REASON_CHARS


IMAGE_SCHEMA_VERSION = "1"
PLATFORM_OWNER = "dspy-trainer"
MANAGED_IMAGE_KIND = "revision-image"
BUNDLE_IMAGE_PATH = "/opt/dspy-bundle"
PYTHON_MANIFEST_PATH = "/opt/dspy-trainer/python-manifest.txt"
GENERATED_ENTRYPOINT_NAME = "dspy-trainer-endpoint-worker"
GENERATED_ENTRYPOINT_PATH = f"/usr/local/bin/{GENERATED_ENTRYPOINT_NAME}"

LABEL_PLATFORM_OWNER = "io.dspy-trainer.platform-owner"
LABEL_OWNER = "io.dspy-trainer.owner"
LABEL_MANAGED_KIND = "io.dspy-trainer.managed-kind"
LABEL_MODULE_ID = "io.dspy-trainer.module-id"
LABEL_REVISION_ID = "io.dspy-trainer.revision-id"
LABEL_BUILD_ID = "io.dspy-trainer.build-id"
LABEL_BUILD_GENERATION = "io.dspy-trainer.build-generation"
LABEL_SOURCE_COMMIT = "io.dspy-trainer.source-commit"
LABEL_SOURCE_CONTENT_DIGEST = "io.dspy-trainer.source-content-digest"
LABEL_DEPENDENCY_DIGEST = "io.dspy-trainer.dependency-digest"
LABEL_BUILD_DIGEST = "io.dspy-trainer.build-digest"
LABEL_BASE_IMAGE_ID = "io.dspy-trainer.base-image-id"
LABEL_PLATFORM_VERSION = "io.dspy-trainer.platform-version"
LABEL_SCHEMA_VERSION = "io.dspy-trainer.schema-version"

REQUIRED_IMAGE_LABELS = frozenset(
    {
        LABEL_PLATFORM_OWNER,
        LABEL_OWNER,
        LABEL_MANAGED_KIND,
        LABEL_MODULE_ID,
        LABEL_REVISION_ID,
        LABEL_BUILD_ID,
        LABEL_BUILD_GENERATION,
        LABEL_SOURCE_COMMIT,
        LABEL_SOURCE_CONTENT_DIGEST,
        LABEL_DEPENDENCY_DIGEST,
        LABEL_BUILD_DIGEST,
        LABEL_BASE_IMAGE_ID,
        LABEL_PLATFORM_VERSION,
        LABEL_SCHEMA_VERSION,
    }
)

_HARD_EXCLUDED_NAMES = frozenset(
    {
        ".aws",
        ".docker",
        ".git",
        ".gnupg",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".ssh",
        ".svn",
        "__pycache__",
    }
)
_SECRET_FILE_NAMES = frozenset(
    {
        ".env",
        ".envrc",
        ".htpasswd",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "auth.json",
        "credentials",
        "credentials.json",
        "secrets.json",
        "service-account.json",
        "service_account.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
    }
)
_SECRET_FILE_SUFFIXES = (".key", ".kdbx", ".pem", ".p12", ".pfx", ".secret", ".secrets", ".token")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x1b]*(?:\x1b\\|\x07))")


class RevisionImageBuildError(ValueError):
    pass


class BuildContextError(RevisionImageBuildError):
    pass


class ImageVerificationError(RevisionImageBuildError):
    pass


@dataclass(frozen=True)
class RevisionImageBuildSpec:
    owner: str
    module_id: str
    revision_id: str
    build_id: str
    generation: int
    source_commit: str
    source_snapshot_path: Path
    source_content_digest: str
    base_image_id: str
    image_repository: str
    platform_version: str


@dataclass(frozen=True)
class GeneratedBuildContext:
    local_tag: str
    build_digest: str
    dependency_digest: str
    labels: Mapping[str, str]
    members: tuple[str, ...]
    dockerfile: str


@dataclass(frozen=True)
class DockerBuildEvent:
    message: str = ""
    image_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class DockerImageInspection:
    image_id: str
    labels: Mapping[str, str]
    repo_tags: tuple[str, ...] = ()
    repo_digests: tuple[str, ...] = ()


class DockerImageAdapter(Protocol):
    def build_image(
        self,
        context: BinaryIO,
        *,
        tag: str,
        labels: Mapping[str, str],
        use_cache: bool,
    ) -> Iterable[DockerBuildEvent]: ...

    def inspect_image(self, image_id: str) -> DockerImageInspection: ...


@dataclass(frozen=True)
class RevisionImageBuildResult:
    status: str
    local_tag: str | None
    build_digest: str | None
    dependency_digest: str | None
    image_id: str | None
    image_digest: str | None
    labels: Mapping[str, str]
    build_log: str
    failure_reason: str | None


@dataclass(frozen=True)
class _SourceSettings:
    include_files: frozenset[PurePosixPath]
    system_dependency_commands: tuple[str, ...]
    requirements_bytes: bytes


@dataclass(frozen=True)
class _SourceEntry:
    path: Path
    relative: PurePosixPath
    kind: str
    mode: int
    size: int
    inode: int
    mtime_ns: int
    link_target: str | None = None


class _HashingReader(io.RawIOBase):
    def __init__(self, source: BinaryIO, digest: Any) -> None:
        self._source = source
        self._digest = digest
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        chunk = self._source.read(size)
        if chunk:
            self._digest.update(chunk)
            self.bytes_read += len(chunk)
        return chunk


class _BoundedLog:
    _TRUNCATION_MARKER = b"[earlier build output truncated]\n"

    def __init__(self, max_bytes: int = MAX_BUILD_LOG_BYTES) -> None:
        self.max_bytes = max_bytes
        self._value = bytearray()

    def append(self, value: object) -> None:
        text = _sanitize_log_text(value)
        if not text:
            return
        self._value.extend(text.encode("utf-8", errors="replace"))
        if len(self._value) <= self.max_bytes:
            return
        tail_size = max(0, self.max_bytes - len(self._TRUNCATION_MARKER))
        tail_bytes = bytes(self._value[-tail_size:]) if tail_size else b""
        valid_tail = tail_bytes.decode("utf-8", errors="ignore").encode("utf-8")
        self._value = bytearray(self._TRUNCATION_MARKER + valid_tail)

    def value(self) -> str:
        return bytes(self._value).decode("utf-8")


class DockerSdkImageAdapter:
    """Narrow Docker SDK boundary used only for local image build and inspection."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> "DockerSdkImageAdapter":
        import docker

        return cls(docker.from_env())

    def build_image(
        self,
        context: BinaryIO,
        *,
        tag: str,
        labels: Mapping[str, str],
        use_cache: bool,
    ) -> Iterable[DockerBuildEvent]:
        context.seek(0)
        stream = self._client.api.build(
            fileobj=context,
            custom_context=True,
            tag=tag,
            labels=dict(labels),
            decode=True,
            pull=False,
            nocache=not use_cache,
            rm=True,
            forcerm=True,
        )
        return self._build_events(stream, tag=tag)

    def _build_events(self, stream: Iterable[object], *, tag: str) -> Iterator[DockerBuildEvent]:
        returned_image_id: str | None = None
        saw_error = False
        for raw_event in stream:
            if not isinstance(raw_event, Mapping):
                yield DockerBuildEvent(message=str(raw_event))
                continue
            aux = raw_event.get("aux")
            if isinstance(aux, Mapping):
                candidate = str(aux.get("ID") or aux.get("Id") or "").strip()
                if candidate:
                    returned_image_id = candidate
            error = _docker_event_error(raw_event)
            if error:
                saw_error = True
            yield DockerBuildEvent(
                message=_docker_event_message(raw_event),
                image_id=returned_image_id if isinstance(aux, Mapping) else None,
                error=error,
            )
        if returned_image_id is None and not saw_error:
            attrs = self._client.api.inspect_image(tag)
            returned_image_id = str(attrs.get("Id") or attrs.get("ID") or "").strip()
        if returned_image_id:
            yield DockerBuildEvent(image_id=returned_image_id)

    def inspect_image(self, image_id: str) -> DockerImageInspection:
        attrs = self._client.api.inspect_image(image_id)
        config = attrs.get("Config") if isinstance(attrs.get("Config"), Mapping) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), Mapping) else {}
        return DockerImageInspection(
            image_id=str(attrs.get("Id") or attrs.get("ID") or "").strip(),
            labels={str(key): str(value) for key, value in labels.items()},
            repo_tags=tuple(str(value) for value in (attrs.get("RepoTags") or ())),
            repo_digests=tuple(str(value) for value in (attrs.get("RepoDigests") or ())),
        )


class RevisionImageBuilder:
    def __init__(self, docker: DockerImageAdapter) -> None:
        self._docker = docker

    def build(self, spec: RevisionImageBuildSpec) -> RevisionImageBuildResult:
        log = _BoundedLog()
        context_metadata: GeneratedBuildContext | None = None
        try:
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as context:
                context_metadata = write_build_context(spec, context)
                base_inspection = self._docker.inspect_image(spec.base_image_id)
                if base_inspection.image_id != spec.base_image_id:
                    raise RevisionImageBuildError(
                        "configured backend base image did not resolve locally to its immutable ID: "
                        f"expected {spec.base_image_id}, found {base_inspection.image_id or '<empty>'}"
                    )
                log.append(f"resolved local base image {spec.base_image_id}\n")
                log.append(f"building {context_metadata.local_tag}\n")
                returned_image_id: str | None = None
                build_error: str | None = None
                for event in self._docker.build_image(
                    context,
                    tag=context_metadata.local_tag,
                    labels=context_metadata.labels,
                    use_cache=True,
                ):
                    log.append(event.message)
                    if event.image_id:
                        returned_image_id = event.image_id
                    if event.error and build_error is None:
                        build_error = event.error
                if build_error is not None:
                    raise RevisionImageBuildError(build_error)
                if returned_image_id is None:
                    raise RevisionImageBuildError("Docker build completed without returning an image ID")
                _require_sha256("returned image_id", returned_image_id)
                inspection = self._docker.inspect_image(returned_image_id)
                verify_built_image(
                    inspection,
                    returned_image_id=returned_image_id,
                    expected_tag=context_metadata.local_tag,
                    expected_labels=context_metadata.labels,
                )
                image_digest = inspection.repo_digests[0] if inspection.repo_digests else None
                log.append(f"verified image {returned_image_id}\n")
                return RevisionImageBuildResult(
                    status="ready",
                    local_tag=context_metadata.local_tag,
                    build_digest=context_metadata.build_digest,
                    dependency_digest=context_metadata.dependency_digest,
                    image_id=returned_image_id,
                    image_digest=image_digest,
                    labels=context_metadata.labels,
                    build_log=log.value(),
                    failure_reason=None,
                )
        except Exception as exc:
            failure_reason = _bounded_failure_reason(exc)
            log.append(f"error: {failure_reason}\n")
            return RevisionImageBuildResult(
                status="failed",
                local_tag=context_metadata.local_tag if context_metadata is not None else None,
                build_digest=context_metadata.build_digest if context_metadata is not None else None,
                dependency_digest=context_metadata.dependency_digest if context_metadata is not None else None,
                image_id=None,
                image_digest=None,
                labels=context_metadata.labels if context_metadata is not None else {},
                build_log=log.value(),
                failure_reason=failure_reason,
            )


def calculate_source_content_digest(snapshot_path: Path | str) -> str:
    root = _validated_snapshot_root(snapshot_path)
    settings = _load_source_settings(root)
    digest = hashlib.sha256()
    for entry in _collect_source_entries(root, settings.include_files):
        _hash_source_entry(entry, digest)
    return f"sha256:{digest.hexdigest()}"


def write_build_context(spec: RevisionImageBuildSpec, output: BinaryIO) -> GeneratedBuildContext:
    _validate_spec(spec)
    root = _validated_snapshot_root(spec.source_snapshot_path)
    settings = _load_source_settings(root)
    dependency_digest = _dependency_digest(settings)
    build_digest = _build_digest(spec, dependency_digest)
    local_tag = _local_tag(spec.image_repository, spec.revision_id, build_digest)
    labels = _build_labels(spec, dependency_digest, build_digest)
    dockerfile = _generated_dockerfile(
        base_image_id=spec.base_image_id,
        labels=labels,
        system_dependency_commands=settings.system_dependency_commands,
        has_requirements=bool(settings.requirements_bytes),
    )
    entrypoint = _generated_entrypoint()
    entries = _collect_source_entries(root, settings.include_files)

    output.seek(0)
    output.truncate(0)
    source_digest = hashlib.sha256()
    members: list[str] = []
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        _add_bytes(archive, "bundle/", b"", mode=0o755, type_=tarfile.DIRTYPE)
        members.append("bundle/")
        for entry in entries:
            archive_name = f"bundle/{entry.relative.as_posix()}"
            if entry.kind == "directory":
                archive_name = f"{archive_name}/"
                _hash_source_entry(entry, source_digest)
                _add_bytes(archive, archive_name, b"", mode=entry.mode, type_=tarfile.DIRTYPE)
            elif entry.kind == "symlink":
                _hash_source_entry(entry, source_digest)
                _add_symlink(archive, archive_name, entry.link_target or "")
            else:
                _add_source_file(archive, archive_name, entry, source_digest)
            members.append(archive_name)
        _add_bytes(archive, "Dockerfile", dockerfile.encode("utf-8"), mode=0o644)
        members.append("Dockerfile")
        _add_bytes(archive, GENERATED_ENTRYPOINT_NAME, entrypoint.encode("utf-8"), mode=0o755)
        members.append(GENERATED_ENTRYPOINT_NAME)
    output.flush()
    output.seek(0)

    actual_source_digest = f"sha256:{source_digest.hexdigest()}"
    if actual_source_digest != spec.source_content_digest:
        raise BuildContextError(
            "revision snapshot content digest mismatch: "
            f"expected {spec.source_content_digest}, calculated {actual_source_digest}"
        )
    return GeneratedBuildContext(
        local_tag=local_tag,
        build_digest=build_digest,
        dependency_digest=dependency_digest,
        labels=labels,
        members=tuple(members),
        dockerfile=dockerfile,
    )


def require_managed_revision_image(
    inspection: DockerImageInspection,
    *,
    owner: str | None = None,
) -> Mapping[str, str]:
    labels = {str(key): str(value) for key, value in inspection.labels.items()}
    missing = sorted(key for key in REQUIRED_IMAGE_LABELS if not labels.get(key, "").strip())
    if missing:
        raise ImageVerificationError(f"image is missing required DSPy Trainer labels: {', '.join(missing)}")
    if labels[LABEL_PLATFORM_OWNER] != PLATFORM_OWNER:
        raise ImageVerificationError(
            f"unexpected platform owner: {labels[LABEL_PLATFORM_OWNER]!r}"
        )
    if labels[LABEL_MANAGED_KIND] != MANAGED_IMAGE_KIND:
        raise ImageVerificationError(
            f"unexpected managed image kind: {labels[LABEL_MANAGED_KIND]!r}"
        )
    if owner is not None and labels[LABEL_OWNER] != owner:
        raise ImageVerificationError(
            f"image owner label mismatch: expected {owner!r}, found {labels[LABEL_OWNER]!r}"
        )
    return labels


def verify_built_image(
    inspection: DockerImageInspection,
    *,
    returned_image_id: str,
    expected_tag: str,
    expected_labels: Mapping[str, str],
) -> None:
    if not returned_image_id.strip():
        raise ImageVerificationError("Docker returned an empty image ID")
    if inspection.image_id != returned_image_id:
        raise ImageVerificationError(
            f"inspected image ID mismatch: expected {returned_image_id}, found {inspection.image_id or '<empty>'}"
        )
    labels = require_managed_revision_image(
        inspection,
        owner=expected_labels.get(LABEL_OWNER),
    )
    mismatches = sorted(
        key
        for key, expected_value in expected_labels.items()
        if labels.get(key) != expected_value
    )
    if mismatches:
        raise ImageVerificationError(
            f"image provenance label mismatch: {', '.join(mismatches)}"
        )
    if inspection.repo_tags and expected_tag not in inspection.repo_tags:
        raise ImageVerificationError(
            f"inspected image does not carry expected local tag: {expected_tag}"
        )


def _validated_snapshot_root(snapshot_path: Path | str) -> Path:
    supplied = Path(snapshot_path).expanduser()
    if supplied.is_symlink():
        raise BuildContextError("revision snapshot root must not be a symlink")
    root = supplied.resolve()
    if not root.exists():
        raise BuildContextError(f"revision snapshot does not exist: {supplied}")
    if not root.is_dir():
        raise BuildContextError(f"revision snapshot must be a directory: {supplied}")
    return root


def _load_source_settings(root: Path) -> _SourceSettings:
    bundle_toml = root / "bundle.toml"
    if not bundle_toml.is_file() or bundle_toml.is_symlink():
        raise BuildContextError("revision snapshot must contain a regular bundle.toml")
    try:
        payload = tomllib.loads(bundle_toml.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise BuildContextError(f"unable to read bundle.toml: {exc}") from exc

    image_build = payload.get("image_build")
    if image_build is None:
        image_build = {}
    if not isinstance(image_build, dict):
        raise BuildContextError("bundle.toml image_build must be a table when provided")
    raw_include_files = image_build.get("include_files", [])
    if not isinstance(raw_include_files, list) or any(not isinstance(value, str) for value in raw_include_files):
        raise BuildContextError("bundle.toml image_build.include_files must be an array of relative paths")
    include_files: set[PurePosixPath] = set()
    for raw_path in raw_include_files:
        relative = _validated_relative_metadata_path(raw_path)
        if _has_hard_excluded_component(relative):
            raise BuildContextError(
                f"bundle.toml cannot override platform control-file exclusion: {relative.as_posix()}"
            )
        candidate = root.joinpath(*relative.parts)
        if not os.path.lexists(candidate):
            raise BuildContextError(
                f"bundle.toml image_build.include_files path does not exist: {relative.as_posix()}"
            )
        if candidate.is_dir() and not candidate.is_symlink():
            raise BuildContextError(
                f"bundle.toml image_build.include_files must name files, not directories: {relative.as_posix()}"
            )
        include_files.add(relative)

    runtime = payload.get("runtime")
    if runtime is None:
        runtime = {}
    if not isinstance(runtime, dict):
        raise BuildContextError("bundle.toml runtime must be a table when provided")
    raw_commands = runtime.get("system_dependency_commands", [])
    if not isinstance(raw_commands, list) or any(
        not isinstance(command, str) or not command.strip() for command in raw_commands
    ):
        raise BuildContextError(
            "bundle.toml runtime.system_dependency_commands must be an array of non-empty strings"
        )
    commands = tuple(command.strip() for command in raw_commands)
    requirements_path = root / "requirements.txt"
    if requirements_path.is_symlink():
        _validate_symlink(root, requirements_path, PurePosixPath("requirements.txt"))
    requirements_bytes = (
        requirements_path.read_bytes()
        if requirements_path.exists() and requirements_path.is_file()
        else b""
    )
    return _SourceSettings(
        include_files=frozenset(include_files),
        system_dependency_commands=commands,
        requirements_bytes=requirements_bytes,
    )


def _validated_relative_metadata_path(value: str) -> PurePosixPath:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or normalized.endswith("/"):
        raise BuildContextError(
            f"bundle.toml image_build.include_files path must name an in-root file: {value!r}"
        )
    if any(part in {"", ".", ".."} for part in path.parts):
        raise BuildContextError(
            f"bundle.toml image_build.include_files path must not escape the bundle root: {value!r}"
        )
    return path


def _collect_source_entries(root: Path, include_files: frozenset[PurePosixPath]) -> tuple[_SourceEntry, ...]:
    entries: list[_SourceEntry] = []
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directory_names.sort()
        file_names.sort()
        retained_directories: list[str] = []
        for name in directory_names:
            path = current_path / name
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if _is_excluded(relative, include_files):
                continue
            entry = _source_entry(root, path, relative)
            entries.append(entry)
            if entry.kind == "directory":
                retained_directories.append(name)
        directory_names[:] = retained_directories
        for name in file_names:
            path = current_path / name
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if _is_excluded(relative, include_files):
                continue
            entries.append(_source_entry(root, path, relative))
    entries.sort(key=lambda entry: entry.relative.as_posix())
    return tuple(entries)


def _source_entry(root: Path, path: Path, relative: PurePosixPath) -> _SourceEntry:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        target = _validate_symlink(root, path, relative)
        return _SourceEntry(
            path=path,
            relative=relative,
            kind="symlink",
            mode=0o777,
            size=0,
            inode=info.st_ino,
            mtime_ns=info.st_mtime_ns,
            link_target=target,
        )
    if stat.S_ISDIR(info.st_mode):
        return _SourceEntry(
            path=path,
            relative=relative,
            kind="directory",
            mode=0o755,
            size=0,
            inode=info.st_ino,
            mtime_ns=info.st_mtime_ns,
        )
    if stat.S_ISREG(info.st_mode):
        return _SourceEntry(
            path=path,
            relative=relative,
            kind="file",
            mode=0o755 if info.st_mode & 0o111 else 0o644,
            size=info.st_size,
            inode=info.st_ino,
            mtime_ns=info.st_mtime_ns,
        )
    raise BuildContextError(f"unsupported file type in revision snapshot: {relative.as_posix()}")


def _validate_symlink(root: Path, path: Path, relative: PurePosixPath) -> str:
    target = os.readlink(path)
    if os.path.isabs(target):
        raise BuildContextError(
            f"absolute symlink is not permitted in revision snapshot: {relative.as_posix()} -> {target}"
        )
    resolved_target = (path.parent / target).resolve(strict=False)
    try:
        resolved_target.relative_to(root)
    except ValueError as exc:
        raise BuildContextError(
            f"symlink escapes revision snapshot: {relative.as_posix()} -> {target}"
        ) from exc
    return target


def _is_excluded(relative: PurePosixPath, include_files: frozenset[PurePosixPath]) -> bool:
    if _has_hard_excluded_component(relative):
        return True
    if relative in include_files:
        return False
    name = relative.name.lower()
    return (
        name in _SECRET_FILE_NAMES
        or name.startswith(".env.")
        or name.endswith(_SECRET_FILE_SUFFIXES)
        or name.endswith(".pyc")
    )


def _has_hard_excluded_component(relative: PurePosixPath) -> bool:
    return any(part.lower() in _HARD_EXCLUDED_NAMES for part in relative.parts)


def _hash_source_entry(entry: _SourceEntry, digest: Any) -> None:
    _hash_entry_header(entry, digest)
    if entry.kind != "file":
        return
    with entry.path.open("rb") as source:
        before = os.fstat(source.fileno())
        _require_unchanged_source(entry, before)
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
        after = os.fstat(source.fileno())
        _require_unchanged_source(entry, after)


def _hash_entry_header(entry: _SourceEntry, digest: Any) -> None:
    digest.update(entry.kind.encode("ascii"))
    digest.update(b"\0")
    digest.update(entry.relative.as_posix().encode("utf-8"))
    digest.update(b"\0")
    digest.update(f"{entry.mode:o}".encode("ascii"))
    digest.update(b"\0")
    if entry.kind == "symlink":
        digest.update((entry.link_target or "").encode("utf-8"))
    elif entry.kind == "file":
        digest.update(str(entry.size).encode("ascii"))
    digest.update(b"\n")


def _add_source_file(archive: tarfile.TarFile, name: str, entry: _SourceEntry, digest: Any) -> None:
    _hash_entry_header(entry, digest)
    info = _tar_info(name, mode=entry.mode, size=entry.size)
    with entry.path.open("rb") as source:
        before = os.fstat(source.fileno())
        _require_unchanged_source(entry, before)
        reader = _HashingReader(source, digest)
        archive.addfile(info, reader)
        after = os.fstat(source.fileno())
        _require_unchanged_source(entry, after)
        if reader.bytes_read != entry.size:
            raise BuildContextError(
                f"revision snapshot file changed while generating context: {entry.relative.as_posix()}"
            )


def _require_unchanged_source(entry: _SourceEntry, observed: os.stat_result) -> None:
    if (
        observed.st_ino != entry.inode
        or observed.st_size != entry.size
        or observed.st_mtime_ns != entry.mtime_ns
        or not stat.S_ISREG(observed.st_mode)
    ):
        raise BuildContextError(
            f"revision snapshot file changed while generating context: {entry.relative.as_posix()}"
        )


def _add_bytes(
    archive: tarfile.TarFile,
    name: str,
    content: bytes,
    *,
    mode: int,
    type_: bytes = tarfile.REGTYPE,
) -> None:
    size = len(content) if type_ == tarfile.REGTYPE else 0
    info = _tar_info(name, mode=mode, size=size, type_=type_)
    archive.addfile(info, io.BytesIO(content) if size else None)


def _add_symlink(archive: tarfile.TarFile, name: str, target: str) -> None:
    info = _tar_info(name, mode=0o777, size=0, type_=tarfile.SYMTYPE)
    info.linkname = target
    archive.addfile(info)


def _tar_info(
    name: str,
    *,
    mode: int,
    size: int,
    type_: bytes = tarfile.REGTYPE,
) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.mode = mode
    info.size = size
    info.type = type_
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def _dependency_digest(settings: _SourceSettings) -> str:
    payload = {
        "requirements_sha256": hashlib.sha256(settings.requirements_bytes).hexdigest()
        if settings.requirements_bytes
        else None,
        "system_dependency_commands": list(settings.system_dependency_commands),
    }
    return f"sha256:{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


def _build_digest(spec: RevisionImageBuildSpec, dependency_digest: str) -> str:
    payload = {
        "base_image_id": spec.base_image_id,
        "build_id": spec.build_id,
        "dependency_digest": dependency_digest,
        "generation": spec.generation,
        "image_repository": spec.image_repository,
        "module_id": spec.module_id,
        "owner": spec.owner,
        "platform_version": spec.platform_version,
        "revision_id": spec.revision_id,
        "schema_version": IMAGE_SCHEMA_VERSION,
        "source_commit": spec.source_commit,
        "source_content_digest": spec.source_content_digest,
    }
    return f"sha256:{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _local_tag(repository: str, revision_id: str, build_digest: str) -> str:
    normalized_revision = re.sub(r"[^a-z0-9_.-]+", "-", revision_id.lower()).strip(".-")
    if not normalized_revision:
        normalized_revision = "revision"
    normalized_revision = normalized_revision[:80].rstrip(".-") or "revision"
    return f"{repository}:{normalized_revision}-{build_digest.removeprefix('sha256:')[:24]}"


def _build_labels(
    spec: RevisionImageBuildSpec,
    dependency_digest: str,
    build_digest: str,
) -> dict[str, str]:
    return {
        LABEL_PLATFORM_OWNER: PLATFORM_OWNER,
        LABEL_OWNER: spec.owner,
        LABEL_MANAGED_KIND: MANAGED_IMAGE_KIND,
        LABEL_MODULE_ID: spec.module_id,
        LABEL_REVISION_ID: spec.revision_id,
        LABEL_BUILD_ID: spec.build_id,
        LABEL_BUILD_GENERATION: str(spec.generation),
        LABEL_SOURCE_COMMIT: spec.source_commit,
        LABEL_SOURCE_CONTENT_DIGEST: spec.source_content_digest,
        LABEL_DEPENDENCY_DIGEST: dependency_digest,
        LABEL_BUILD_DIGEST: build_digest,
        LABEL_BASE_IMAGE_ID: spec.base_image_id,
        LABEL_PLATFORM_VERSION: spec.platform_version,
        LABEL_SCHEMA_VERSION: IMAGE_SCHEMA_VERSION,
    }


def _generated_dockerfile(
    *,
    base_image_id: str,
    labels: Mapping[str, str],
    system_dependency_commands: tuple[str, ...],
    has_requirements: bool,
) -> str:
    lines = [f"FROM {base_image_id}"]
    for key in sorted(labels):
        lines.append(f"LABEL {key}={json.dumps(labels[key], ensure_ascii=True)}")
    lines.extend(
        [
            f"WORKDIR {BUNDLE_IMAGE_PATH}",
            f"COPY bundle/ {BUNDLE_IMAGE_PATH}/",
        ]
    )
    for command in system_dependency_commands:
        lines.append(f"RUN {json.dumps(['/bin/sh', '-eu', '-c', command], separators=(',', ':'))}")
    if has_requirements:
        lines.append(
            "RUN "
            + json.dumps(
                [
                    "/bin/sh",
                    "-eu",
                    "-c",
                    "python -m pip install --no-cache-dir -r requirements.txt",
                ],
                separators=(",", ":"),
            )
        )
    lines.extend(
        [
            "RUN "
            + json.dumps(
                [
                    "/bin/sh",
                    "-eu",
                    "-c",
                    f"mkdir -p {Path(PYTHON_MANIFEST_PATH).parent.as_posix()} && "
                    f"python -m pip freeze --all | LC_ALL=C sort > {PYTHON_MANIFEST_PATH}",
                ],
                separators=(",", ":"),
            ),
            f"COPY {GENERATED_ENTRYPOINT_NAME} {GENERATED_ENTRYPOINT_PATH}",
            f"RUN chmod 0555 {GENERATED_ENTRYPOINT_PATH}",
            f"ENV DSPY_TRAINER_BUNDLE_PATH={BUNDLE_IMAGE_PATH}",
            f"ENTRYPOINT {json.dumps([GENERATED_ENTRYPOINT_PATH], separators=(',', ':'))}",
            "CMD []",
            "",
        ]
    )
    return "\n".join(lines)


def _generated_entrypoint() -> str:
    return "\n".join(
        [
            "#!/bin/sh",
            "set -eu",
            f"export DSPY_TRAINER_BUNDLE_PATH={BUNDLE_IMAGE_PATH}",
            'exec python /app/backend/endpoint_worker.py "$@"',
            "",
        ]
    )


def _validate_spec(spec: RevisionImageBuildSpec) -> None:
    for field_name in (
        "owner",
        "module_id",
        "revision_id",
        "build_id",
        "source_commit",
        "image_repository",
        "platform_version",
    ):
        value = str(getattr(spec, field_name) or "")
        if not value.strip() or value != value.strip() or any(character in value for character in "\r\n\0"):
            raise RevisionImageBuildError(f"{field_name} must be a non-empty single-line value")
    if spec.generation < 1:
        raise RevisionImageBuildError("generation must be at least 1")
    _require_sha256("source_content_digest", spec.source_content_digest)
    _require_sha256("base_image_id", spec.base_image_id)
    if any(character.isspace() for character in spec.image_repository) or "@" in spec.image_repository:
        raise RevisionImageBuildError("image_repository must be a local Docker repository name")


def _require_sha256(field_name: str, value: str) -> None:
    if not _SHA256_PATTERN.fullmatch(str(value or "")):
        raise RevisionImageBuildError(f"{field_name} must be an immutable sha256 digest")


def _sanitize_log_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = _ANSI_ESCAPE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return _CONTROL_CHARACTERS.sub("", text)


def _bounded_failure_reason(exc: Exception) -> str:
    reason = _sanitize_log_text(exc).strip() or exc.__class__.__name__
    if len(reason) <= MAX_FAILURE_REASON_CHARS:
        return reason
    return f"{reason[: MAX_FAILURE_REASON_CHARS - 1]}…"


def _docker_event_error(event: Mapping[str, object]) -> str | None:
    error = event.get("error")
    if error:
        return _sanitize_log_text(error).strip()
    detail = event.get("errorDetail")
    if isinstance(detail, Mapping) and detail.get("message"):
        return _sanitize_log_text(detail["message"]).strip()
    return None


def _docker_event_message(event: Mapping[str, object]) -> str:
    stream = event.get("stream")
    if stream:
        return _sanitize_log_text(stream)
    error = _docker_event_error(event)
    if error:
        return f"{error}\n"
    status = _sanitize_log_text(event.get("status")).strip()
    progress = _sanitize_log_text(event.get("progress")).strip()
    if status and progress:
        return f"{status} {progress}\n"
    if status:
        return f"{status}\n"
    return ""
