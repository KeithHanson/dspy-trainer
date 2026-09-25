from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys
import tarfile
from types import SimpleNamespace

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.revision_image_builder import (
    BUNDLE_IMAGE_PATH,
    DockerBuildEvent,
    DockerImageInspection,
    DockerSdkImageAdapter,
    GENERATED_ENTRYPOINT_NAME,
    LABEL_BASE_IMAGE_ID,
    LABEL_BUILD_DIGEST,
    LABEL_BUILD_GENERATION,
    LABEL_BUILD_ID,
    LABEL_DEPENDENCY_DIGEST,
    LABEL_MANAGED_KIND,
    LABEL_MODULE_ID,
    LABEL_OWNER,
    LABEL_PLATFORM_OWNER,
    LABEL_PLATFORM_VERSION,
    LABEL_REVISION_ID,
    LABEL_SCHEMA_VERSION,
    LABEL_SOURCE_COMMIT,
    LABEL_SOURCE_CONTENT_DIGEST,
    MANAGED_IMAGE_KIND,
    PLATFORM_OWNER,
    PYTHON_MANIFEST_PATH,
    REQUIRED_IMAGE_LABELS,
    BuildContextError,
    ImageVerificationError,
    RevisionImageBuildSpec,
    RevisionImageBuilder,
    calculate_source_content_digest,
    require_managed_revision_image,
    write_build_context,
)
from app.revision_images import MAX_BUILD_LOG_BYTES


BASE_IMAGE_ID = f"sha256:{'a' * 64}"
IMAGE_ID = f"sha256:{'b' * 64}"


def _write_bundle(root: Path, *, include_files: tuple[str, ...] = ()) -> None:
    root.mkdir(parents=True, exist_ok=True)
    include_line = ""
    if include_files:
        values = ", ".join(f'"{value}"' for value in include_files)
        include_line = f"\n[image_build]\ninclude_files = [{values}]\n"
    (root / "bundle.toml").write_text(
        "\n".join(
            [
                'name = "fixture"',
                'version = "1.0.0"',
                "score_pass_threshold = 0.5",
                "",
                "[runtime]",
                'system_dependency_commands = ["apt-get update", "apt-get install -y curl"]',
            ]
        )
        + include_line,
        encoding="utf-8",
    )
    (root / "module.py").write_text("VALUE = 'module'\n", encoding="utf-8")
    (root / "metric.py").write_text("VALUE = 'metric'\n", encoding="utf-8")
    (root / "requirements.txt").write_text("example-package==1.2.3\n", encoding="utf-8")


def _spec(
    root: Path,
    *,
    generation: int = 1,
    build_id: str = "build-1",
    source_content_digest: str | None = None,
) -> RevisionImageBuildSpec:
    return RevisionImageBuildSpec(
        owner="compose-project-a",
        module_id="module-a",
        revision_id="revision/A",
        build_id=build_id,
        generation=generation,
        source_commit="0123456789abcdef",
        source_snapshot_path=root,
        source_content_digest=source_content_digest or calculate_source_content_digest(root),
        base_image_id=BASE_IMAGE_ID,
        image_repository="dspy-trainer-module",
        platform_version="2026.09",
    )


def _tar_members(payload: bytes) -> tuple[dict[str, tarfile.TarInfo], dict[str, bytes]]:
    infos: dict[str, tarfile.TarInfo] = {}
    contents: dict[str, bytes] = {}
    with tarfile.open(fileobj=BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            infos[member.name] = member
            extracted = archive.extractfile(member) if member.isfile() else None
            if extracted is not None:
                contents[member.name] = extracted.read()
    return infos, contents


class _FakeDocker:
    def __init__(
        self,
        *,
        image_id: str = IMAGE_ID,
        events: tuple[DockerBuildEvent, ...] | None = None,
        inspection_labels: dict[str, str] | None = None,
        resolved_base_image_id: str = BASE_IMAGE_ID,
    ) -> None:
        self.image_id = image_id
        self.events = events
        self.inspection_labels = inspection_labels
        self.resolved_base_image_id = resolved_base_image_id
        self.build_calls: list[dict] = []
        self.inspect_calls: list[str] = []

    def build_image(self, context, *, tag, labels, use_cache):
        self.build_calls.append(
            {
                "context": context.read(),
                "tag": tag,
                "labels": dict(labels),
                "use_cache": use_cache,
            }
        )
        if self.events is not None:
            return self.events
        return (
            DockerBuildEvent(message="\x1b[32mStep 1 complete\x1b[0m\r\n"),
            DockerBuildEvent(image_id=self.image_id),
        )

    def inspect_image(self, image_id):
        self.inspect_calls.append(image_id)
        if image_id == BASE_IMAGE_ID:
            return DockerImageInspection(image_id=self.resolved_base_image_id, labels={})
        build_call = self.build_calls[-1]
        labels = self.inspection_labels if self.inspection_labels is not None else build_call["labels"]
        return DockerImageInspection(
            image_id=self.image_id,
            labels=labels,
            repo_tags=(build_call["tag"],),
            repo_digests=(f"dspy-trainer-module@sha256:{'c' * 64}",),
        )


def test_context_is_deterministic_complete_and_excludes_secrets_by_default(tmp_path, monkeypatch):
    root = tmp_path / "snapshot"
    _write_bundle(root, include_files=("fixtures/test.key",))
    (root / "README.md").write_text("bundle documentation\n", encoding="utf-8")
    (root / "Dockerfile").write_text("FROM foreign-image\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "value.txt").write_text("payload\n", encoding="utf-8")
    (root / "alias.txt").symlink_to("data/value.txt")
    (root / "fixtures").mkdir()
    (root / "fixtures" / "test.key").write_text("declared fixture\n", encoding="utf-8")
    (root / ".env").write_text("API_KEY=bundle-secret\n", encoding="utf-8")
    (root / ".env.production").write_text("TOKEN=production-secret\n", encoding="utf-8")
    (root / "private.pem").write_text("private-material\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("checkout metadata\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_PAT", "host-runtime-secret")

    spec = _spec(root)
    first = BytesIO()
    second = BytesIO()
    first_metadata = write_build_context(spec, first)
    second_metadata = write_build_context(spec, second)

    assert first.getvalue() == second.getvalue()
    assert first_metadata == second_metadata
    assert first_metadata.local_tag.startswith("dspy-trainer-module:revision-a-")
    assert first_metadata.labels[LABEL_BASE_IMAGE_ID] == BASE_IMAGE_ID
    assert first_metadata.labels[LABEL_DEPENDENCY_DIGEST].startswith("sha256:")

    infos, contents = _tar_members(first.getvalue())
    assert "Dockerfile" in infos
    assert GENERATED_ENTRYPOINT_NAME in infos
    assert "bundle/Dockerfile" in infos
    assert "bundle/README.md" in infos
    assert "bundle/data/value.txt" in infos
    assert "bundle/fixtures/test.key" in infos
    assert "bundle/.env" not in infos
    assert "bundle/.env.production" not in infos
    assert "bundle/private.pem" not in infos
    assert not any(name.startswith("bundle/.git") for name in infos)
    assert infos["bundle/alias.txt"].issym()
    assert infos["bundle/alias.txt"].linkname == "data/value.txt"
    assert all(member.mtime == 0 and member.uid == 0 and member.gid == 0 for member in infos.values())

    dockerfile = contents["Dockerfile"].decode("utf-8")
    assert dockerfile.startswith(f"FROM {BASE_IMAGE_ID}\n")
    assert dockerfile.index("apt-get update") < dockerfile.index("apt-get install -y curl")
    assert dockerfile.index("apt-get install -y curl") < dockerfile.index("pip install")
    assert dockerfile.index("pip install") < dockerfile.index("pip freeze --all")
    assert f"COPY bundle/ {BUNDLE_IMAGE_PATH}/" in dockerfile
    assert PYTHON_MANIFEST_PATH in dockerfile
    assert f"LABEL {LABEL_OWNER}=\"compose-project-a\"" in dockerfile
    assert f"ENTRYPOINT [\"/usr/local/bin/{GENERATED_ENTRYPOINT_NAME}\"]" in dockerfile
    assert "CMD []" in dockerfile
    assert contents[GENERATED_ENTRYPOINT_NAME].decode("utf-8").endswith(
        'exec python /app/backend/endpoint_worker.py "$@"\n'
    )
    assert b"bundle-secret" not in first.getvalue()
    assert b"production-secret" not in first.getvalue()
    assert b"private-material" not in first.getvalue()
    assert b"host-runtime-secret" not in first.getvalue()
    assert b"declared fixture" in first.getvalue()


def test_context_rejects_symlinks_that_escape_snapshot(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    root = tmp_path / "snapshot"
    _write_bundle(root)
    (root / "escape.txt").symlink_to("../outside.txt")

    with pytest.raises(BuildContextError, match="symlink escapes revision snapshot"):
        calculate_source_content_digest(root)

    fake = _FakeDocker()
    spec = _spec(root, source_content_digest=f"sha256:{'d' * 64}")
    result = RevisionImageBuilder(fake).build(spec)

    assert result.status == "failed"
    assert result.image_id is None
    assert "symlink" in (result.failure_reason or "")
    assert fake.build_calls == []


def test_metadata_override_must_be_explicit_in_root_file(tmp_path):
    root = tmp_path / "snapshot"
    _write_bundle(root, include_files=("../outside.key",))

    with pytest.raises(BuildContextError, match="must not escape"):
        calculate_source_content_digest(root)

    _write_bundle(root, include_files=(".git/config",))
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("metadata\n", encoding="utf-8")
    with pytest.raises(BuildContextError, match="control-file exclusion"):
        calculate_source_content_digest(root)


def test_two_generations_get_distinct_digest_tags_and_verified_immutable_ids(tmp_path):
    root = tmp_path / "snapshot"
    _write_bundle(root)
    digest = calculate_source_content_digest(root)

    first_docker = _FakeDocker(image_id=f"sha256:{'1' * 64}")
    second_docker = _FakeDocker(image_id=f"sha256:{'2' * 64}")
    first = RevisionImageBuilder(first_docker).build(
        _spec(root, generation=1, build_id="build-1", source_content_digest=digest)
    )
    second = RevisionImageBuilder(second_docker).build(
        _spec(root, generation=2, build_id="build-2", source_content_digest=digest)
    )

    assert first.status == second.status == "ready"
    assert first.local_tag != second.local_tag
    assert first.build_digest != second.build_digest
    assert first.image_id == f"sha256:{'1' * 64}"
    assert second.image_id == f"sha256:{'2' * 64}"
    assert first_docker.build_calls[0]["use_cache"] is True
    assert second_docker.build_calls[0]["use_cache"] is True
    assert first_docker.inspect_calls == [BASE_IMAGE_ID, first.image_id]
    assert second_docker.inspect_calls == [BASE_IMAGE_ID, second.image_id]
    assert first.labels[LABEL_BUILD_ID] == "build-1"
    assert first.labels[LABEL_BUILD_GENERATION] == "1"
    assert second.labels[LABEL_BUILD_ID] == "build-2"
    assert second.labels[LABEL_BUILD_GENERATION] == "2"
    assert "\x1b" not in first.build_log
    assert "\r" not in first.build_log


def test_build_failure_has_bounded_useful_logs_and_no_ready_image(tmp_path):
    root = tmp_path / "snapshot"
    _write_bundle(root)
    error = "system dependency command failed with exit code 100"
    docker = _FakeDocker(
        events=(
            DockerBuildEvent(message="☃ old output\n" * (MAX_BUILD_LOG_BYTES // 4)),
            DockerBuildEvent(message=f"{error}\n", error=error),
        )
    )

    result = RevisionImageBuilder(docker).build(_spec(root))

    assert result.status == "failed"
    assert result.image_id is None
    assert result.image_digest is None
    assert result.failure_reason == error
    assert error in result.build_log
    assert len(result.build_log.encode("utf-8")) <= MAX_BUILD_LOG_BYTES
    assert "earlier build output truncated" in result.build_log
    assert docker.inspect_calls == [BASE_IMAGE_ID]


def test_source_digest_mismatch_never_reaches_docker(tmp_path):
    root = tmp_path / "snapshot"
    _write_bundle(root)
    docker = _FakeDocker()

    result = RevisionImageBuilder(docker).build(
        _spec(root, source_content_digest=f"sha256:{'f' * 64}")
    )

    assert result.status == "failed"
    assert "content digest mismatch" in (result.failure_reason or "")
    assert result.image_id is None
    assert docker.build_calls == []


def test_configured_base_must_resolve_to_exact_local_image_id(tmp_path):
    root = tmp_path / "snapshot"
    _write_bundle(root)
    docker = _FakeDocker(resolved_base_image_id=f"sha256:{'e' * 64}")

    result = RevisionImageBuilder(docker).build(_spec(root))

    assert result.status == "failed"
    assert "did not resolve locally" in (result.failure_reason or "")
    assert result.image_id is None
    assert docker.inspect_calls == [BASE_IMAGE_ID]
    assert docker.build_calls == []

def test_missing_or_mismatched_provenance_labels_never_produce_ready_result(tmp_path):
    root = tmp_path / "snapshot"
    _write_bundle(root)
    expected_labels = {
        LABEL_PLATFORM_OWNER: PLATFORM_OWNER,
        LABEL_OWNER: "compose-project-a",
        LABEL_MANAGED_KIND: MANAGED_IMAGE_KIND,
        LABEL_MODULE_ID: "module-a",
        LABEL_REVISION_ID: "revision/A",
        LABEL_BUILD_ID: "build-1",
        LABEL_BUILD_GENERATION: "1",
        LABEL_SOURCE_COMMIT: "0123456789abcdef",
        LABEL_SOURCE_CONTENT_DIGEST: calculate_source_content_digest(root),
        LABEL_DEPENDENCY_DIGEST: f"sha256:{'3' * 64}",
        LABEL_BUILD_DIGEST: f"sha256:{'4' * 64}",
        LABEL_BASE_IMAGE_ID: BASE_IMAGE_ID,
        LABEL_PLATFORM_VERSION: "2026.09",
        LABEL_SCHEMA_VERSION: "1",
    }
    missing_base = dict(expected_labels)
    missing_base.pop(LABEL_BASE_IMAGE_ID)
    docker = _FakeDocker(inspection_labels=missing_base)

    result = RevisionImageBuilder(docker).build(_spec(root))

    assert result.status == "failed"
    assert result.image_id is None
    assert LABEL_BASE_IMAGE_ID in (result.failure_reason or "")
    with pytest.raises(ImageVerificationError, match="missing required"):
        require_managed_revision_image(
            DockerImageInspection(image_id=IMAGE_ID, labels=missing_base),
            owner="compose-project-a",
        )

    wrong_owner = dict(expected_labels)
    wrong_owner[LABEL_OWNER] = "another-stack"
    with pytest.raises(ImageVerificationError, match="owner label mismatch"):
        require_managed_revision_image(
            DockerImageInspection(image_id=IMAGE_ID, labels=wrong_owner),
            owner="compose-project-a",
        )
    assert set(expected_labels) == set(REQUIRED_IMAGE_LABELS)


def test_sdk_adapter_uses_local_cached_host_build_without_build_args():
    class FakeApi:
        def __init__(self):
            self.build_kwargs = None
            self.inspect_refs = []

        def build(self, **kwargs):
            self.build_kwargs = kwargs
            return iter(
                [
                    {"stream": "Step 1/1\n"},
                    {"aux": {"ID": IMAGE_ID}},
                ]
            )

        def inspect_image(self, reference):
            self.inspect_refs.append(reference)
            return {
                "Id": IMAGE_ID,
                "Config": {"Labels": {LABEL_OWNER: "compose-project-a"}},
                "RepoTags": ["dspy-trainer-module:fixture"],
                "RepoDigests": [],
            }

    api = FakeApi()
    adapter = DockerSdkImageAdapter(SimpleNamespace(api=api))
    context = BytesIO(b"tar context")
    events = list(
        adapter.build_image(
            context,
            tag="dspy-trainer-module:fixture",
            labels={LABEL_OWNER: "compose-project-a"},
            use_cache=True,
        )
    )

    assert events[-1].image_id == IMAGE_ID
    assert api.build_kwargs["pull"] is False
    assert api.build_kwargs["nocache"] is False
    assert api.build_kwargs["custom_context"] is True
    assert api.build_kwargs["labels"] == {LABEL_OWNER: "compose-project-a"}
    assert "buildargs" not in api.build_kwargs
    assert "platform" not in api.build_kwargs
    assert "network_mode" not in api.build_kwargs
    inspection = adapter.inspect_image(IMAGE_ID)
    assert inspection.image_id == IMAGE_ID
    assert inspection.labels == {LABEL_OWNER: "compose-project-a"}
    assert api.inspect_refs == [IMAGE_ID]
