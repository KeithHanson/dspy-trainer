from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import site
import sys
import tomllib
from typing import Any


@dataclass(frozen=True)
class BundlePreparationSpec:
    bundle_root: Path
    requirements_path: Path
    requirements_bytes: bytes
    system_dependency_commands: list[str]
    digest: str

    @property
    def has_requirements(self) -> bool:
        return bool(self.requirements_bytes)

    @property
    def has_system_dependency_commands(self) -> bool:
        return bool(self.system_dependency_commands)

    @property
    def has_preparation_work(self) -> bool:
        return self.has_requirements or self.has_system_dependency_commands


def _looks_like_local_reference(value: str) -> bool:
    if not value:
        return False
    lowered = value.lower()
    return (
        value.startswith((".", "/", "~"))
        or lowered.startswith("file:")
        or value.endswith((".whl", ".zip", ".tar.gz", ".tar.bz2", ".tgz"))
    )


def _resolve_requirement_reference(base_dir: Path, reference: str) -> Path | None:
    candidate = reference.strip()
    if not candidate:
        return None
    if ";" in candidate:
        candidate = candidate.split(";", 1)[0].strip()
    if candidate.lower().startswith("file:"):
        candidate = candidate[5:]
        if candidate.startswith("//"):
            candidate = candidate[2:]
    resolved = Path(candidate).expanduser()
    if not resolved.is_absolute():
        resolved = (base_dir / resolved).resolve()
    else:
        resolved = resolved.resolve()
    return resolved


def _fingerprint_path(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if path.is_dir():
        payload: list[dict[str, str]] = []
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            payload.append(
                {
                    "path": child.relative_to(path).as_posix(),
                    "sha256": hashlib.sha256(child.read_bytes()).hexdigest(),
                }
            )
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return "missing"


def _collect_local_requirement_inputs(bundle_root: Path, requirements_path: Path) -> list[dict[str, str]]:
    seen_files: set[Path] = set()
    fingerprints: list[dict[str, str]] = []

    def visit(requirements_file: Path) -> None:
        resolved_requirements = requirements_file.resolve()
        if resolved_requirements in seen_files or not resolved_requirements.exists() or not resolved_requirements.is_file():
            return
        seen_files.add(resolved_requirements)
        fingerprints.append(
            {
                "kind": "requirements_file",
                "path": (
                    resolved_requirements.relative_to(bundle_root).as_posix()
                    if resolved_requirements.is_relative_to(bundle_root)
                    else str(resolved_requirements)
                ),
                "sha256": hashlib.sha256(resolved_requirements.read_bytes()).hexdigest(),
            }
        )

        for raw_line in resolved_requirements.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                tokens = shlex.split(line, comments=True)
            except ValueError:
                tokens = [line.split("#", 1)[0].strip()]
            if not tokens:
                continue

            option = tokens[0]
            include_ref: str | None = None
            local_ref: str | None = None
            if option in {"-r", "--requirement", "-c", "--constraint"} and len(tokens) >= 2:
                include_ref = tokens[1]
            elif option.startswith("--requirement=") or option.startswith("--constraint="):
                include_ref = option.split("=", 1)[1]
            elif option in {"-e", "--editable"} and len(tokens) >= 2:
                local_ref = tokens[1]
            elif option.startswith("--editable="):
                local_ref = option.split("=", 1)[1]
            elif _looks_like_local_reference(option):
                local_ref = option

            if include_ref is not None:
                include_path = _resolve_requirement_reference(resolved_requirements.parent, include_ref)
                if include_path is not None:
                    visit(include_path)
                continue

            if local_ref is None or not _looks_like_local_reference(local_ref):
                continue
            local_path = _resolve_requirement_reference(resolved_requirements.parent, local_ref)
            if local_path is None:
                continue
            fingerprints.append(
                {
                    "kind": "local_requirement",
                    "path": (
                        local_path.relative_to(bundle_root).as_posix()
                        if local_path.is_relative_to(bundle_root)
                        else str(local_path)
                    ),
                    "sha256": _fingerprint_path(local_path),
                }
            )

    visit(requirements_path)
    return fingerprints


def inspect_bundle_preparation(bundle_path: str) -> BundlePreparationSpec:
    root = Path(bundle_path).expanduser().resolve()
    requirements_path = root / "requirements.txt"
    bundle_toml_path = root / "bundle.toml"
    system_dependency_commands: list[str] = []
    if bundle_toml_path.exists() and bundle_toml_path.is_file():
        try:
            payload = tomllib.loads(bundle_toml_path.read_text(encoding="utf-8"))
            runtime_payload = payload.get("runtime")
            if isinstance(runtime_payload, dict):
                raw_commands = runtime_payload.get("system_dependency_commands")
                if isinstance(raw_commands, list):
                    system_dependency_commands = [str(item).strip() for item in raw_commands if isinstance(item, str) and item.strip()]
        except Exception:
            system_dependency_commands = []

    requirements_bytes = requirements_path.read_bytes() if requirements_path.exists() and requirements_path.is_file() else b""
    local_requirement_inputs = _collect_local_requirement_inputs(root, requirements_path) if requirements_bytes else []
    digest = hashlib.sha256(
        json.dumps(
            {
                "requirements_sha256": hashlib.sha256(requirements_bytes).hexdigest() if requirements_bytes else None,
                "local_requirement_inputs": local_requirement_inputs,
                "system_dependency_commands": system_dependency_commands,
                "python_cache_tag": getattr(sys.implementation, "cache_tag", None),
                "python_version": platform.python_version(),
                "platform_system": platform.system(),
                "platform_machine": platform.machine(),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return BundlePreparationSpec(
        bundle_root=root,
        requirements_path=requirements_path,
        requirements_bytes=requirements_bytes,
        system_dependency_commands=system_dependency_commands,
        digest=digest,
    )


def bundle_preparation_cache_root(checkout_root: str) -> Path:
    override = str(os.getenv("DSPY_TRAINER_BUNDLE_PREPARATION_CACHE_ROOT", "")).strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(checkout_root).expanduser().resolve() / ".bundle-preparation-cache"


def bundle_preparation_artifact_dir(cache_root: Path, digest: str) -> Path:
    return cache_root / digest


def bundle_preparation_site_packages_dir(cache_root: Path, digest: str) -> Path:
    return bundle_preparation_artifact_dir(cache_root, digest) / "site-packages"


def bundle_preparation_manifest_path(cache_root: Path, digest: str) -> Path:
    return bundle_preparation_artifact_dir(cache_root, digest) / "prepared.json"


def bundle_preparation_lock_path(cache_root: Path, digest: str) -> Path:
    return cache_root / f"{digest}.lock"


def has_prepared_bundle_artifact(cache_root: Path, digest: str) -> bool:
    return bundle_preparation_manifest_path(cache_root, digest).exists() and bundle_preparation_site_packages_dir(cache_root, digest).exists()


def activate_bundle_preparation(bundle_path: str, checkout_root: str) -> Path | None:
    spec = inspect_bundle_preparation(bundle_path)
    if not spec.has_requirements:
        return None
    cache_root = bundle_preparation_cache_root(checkout_root)
    site_packages_dir = bundle_preparation_site_packages_dir(cache_root, spec.digest)
    if not site_packages_dir.exists() or not site_packages_dir.is_dir():
        return None
    site.addsitedir(str(site_packages_dir))
    return site_packages_dir
