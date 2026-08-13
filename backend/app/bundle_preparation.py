from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
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
    digest = hashlib.sha256(
        json.dumps(
            {
                "requirements_sha256": hashlib.sha256(requirements_bytes).hexdigest() if requirements_bytes else None,
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
