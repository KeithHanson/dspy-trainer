from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


REVISION_IMAGE_BUILD_STATUSES = frozenset(
    {"queued", "building", "ready", "failed", "superseded", "pruned"}
)
ENDPOINT_DEPLOYMENT_PHASES = frozenset(
    {"legacy_static", "pending", "rolling", "ready", "draining", "rollback", "failed"}
)
MANAGED_CONTAINER_LIFECYCLES = frozenset(
    {"created", "starting", "ready", "busy", "draining", "stopped", "failed", "missing", "removed"}
)
MAX_BUILD_LOG_BYTES = 262_144
MAX_FAILURE_REASON_CHARS = 4_096

_BUILD_TRANSITIONS = {
    "queued": frozenset({"building", "failed", "pruned"}),
    "building": frozenset({"queued", "ready", "failed"}),
    "ready": frozenset({"superseded", "pruned"}),
    "failed": frozenset({"pruned"}),
    "superseded": frozenset({"pruned"}),
    "pruned": frozenset(),
}
_DEPLOYMENT_TRANSITIONS = {
    "legacy_static": frozenset({"pending"}),
    "pending": frozenset({"rolling", "rollback", "failed"}),
    "rolling": frozenset({"draining", "ready", "rollback", "failed"}),
    "draining": frozenset({"ready", "rollback", "failed"}),
    "ready": frozenset({"pending", "rolling"}),
    "rollback": frozenset({"draining", "ready", "failed"}),
    "failed": frozenset({"rollback", "pending"}),
}
_CONTAINER_TRANSITIONS = {
    "created": frozenset({"starting", "failed", "removed"}),
    "starting": frozenset({"ready", "failed", "missing", "removed"}),
    "ready": frozenset({"busy", "draining", "failed", "missing", "stopped"}),
    "busy": frozenset({"ready", "draining", "failed", "missing", "stopped"}),
    "draining": frozenset({"stopped", "failed", "missing"}),
    "stopped": frozenset({"removed", "missing"}),
    "failed": frozenset({"removed", "missing"}),
    "missing": frozenset({"removed"}),
    "removed": frozenset(),
}


def _validated_transition(
    current: Any,
    target: Any,
    transitions: Mapping[str, frozenset[str]],
    *,
    contract: str,
) -> str:
    current_value = str(current or "").strip().lower()
    target_value = str(target or "").strip().lower()
    if current_value not in transitions:
        raise ValueError(f"unknown {contract} state: {current_value or '<empty>'}")
    if target_value not in transitions:
        raise ValueError(f"unknown {contract} state: {target_value or '<empty>'}")
    if target_value != current_value and target_value not in transitions[current_value]:
        raise ValueError(f"invalid {contract} transition: {current_value} -> {target_value}")
    return target_value


def validate_revision_image_build_transition(current: Any, target: Any) -> str:
    return _validated_transition(current, target, _BUILD_TRANSITIONS, contract="revision image build")


def validate_endpoint_deployment_transition(current: Any, target: Any) -> str:
    return _validated_transition(current, target, _DEPLOYMENT_TRANSITIONS, contract="endpoint deployment")


def validate_managed_container_transition(current: Any, target: Any) -> str:
    return _validated_transition(current, target, _CONTAINER_TRANSITIONS, contract="managed container")


def _value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_revision_image_payload(row: Any) -> dict[str, Any]:
    return {
        "id": _value(row, "id"),
        "revision_id": _value(row, "revision_id"),
        "generation": int(_value(row, "generation") or 0),
        "source_commit": _value(row, "source_commit"),
        "source_snapshot_path": _value(row, "source_snapshot_path"),
        "source_content_digest": _value(row, "source_content_digest"),
        "local_tag": _value(row, "local_tag"),
        "image_id": _value(row, "image_id"),
        "image_digest": _value(row, "image_digest"),
        "base_image_id": _value(row, "base_image_id"),
        "status": _value(row, "status"),
        "attempt": int(_value(row, "attempt") or 0),
        "available_at": _timestamp(_value(row, "available_at")),
        "claim_owner": _value(row, "claim_owner"),
        "claim_expires_at": _timestamp(_value(row, "claim_expires_at")),
        "build_log": _value(row, "build_log") or "",
        "failure_reason": _value(row, "failure_reason"),
        "retry_of_build_id": _value(row, "retry_of_build_id"),
        "queued_at": _timestamp(_value(row, "queued_at")),
        "started_at": _timestamp(_value(row, "started_at")),
        "finished_at": _timestamp(_value(row, "finished_at")),
        "created_at": _timestamp(_value(row, "created_at")),
        "updated_at": _timestamp(_value(row, "updated_at")),
    }

def build_revision_image_summary_payload(row: Any) -> dict[str, Any]:
    payload = build_revision_image_payload(row)
    payload.pop("build_log", None)
    payload.pop("source_snapshot_path", None)
    return payload


def build_endpoint_deployment_payload(row: Any) -> dict[str, Any]:
    return {
        "id": _value(row, "id"),
        "endpoint_id": _value(row, "endpoint_id"),
        "active_build_id": _value(row, "active_build_id"),
        "active_revision_id": _value(row, "active_revision_id"),
        "target_build_id": _value(row, "target_build_id"),
        "target_revision_id": _value(row, "target_revision_id"),
        "previous_build_id": _value(row, "previous_build_id"),
        "previous_revision_id": _value(row, "previous_revision_id"),
        "phase": _value(row, "phase"),
        "desired_replica_count": int(_value(row, "desired_replica_count") or 0),
        "rollout_generation": int(_value(row, "rollout_generation") or 0),
        "deadline_at": _timestamp(_value(row, "deadline_at")),
        "failure_reason": _value(row, "failure_reason"),
        "rollout_started_at": _timestamp(_value(row, "rollout_started_at")),
        "ready_at": _timestamp(_value(row, "ready_at")),
        "created_at": _timestamp(_value(row, "created_at")),
        "updated_at": _timestamp(_value(row, "updated_at")),
    }


def build_managed_container_payload(row: Any) -> dict[str, Any]:
    return {
        "container_id": _value(row, "container_id"),
        "container_name": _value(row, "container_name"),
        "endpoint_id": _value(row, "endpoint_id"),
        "deployment_id": _value(row, "deployment_id"),
        "build_id": _value(row, "build_id"),
        "revision_id": _value(row, "revision_id"),
        "slot": int(_value(row, "slot") or 0),
        "lifecycle": _value(row, "lifecycle"),
        "last_observed_at": _timestamp(_value(row, "last_observed_at")),
        "last_heartbeat_at": _timestamp(_value(row, "last_heartbeat_at")),
        "started_at": _timestamp(_value(row, "started_at")),
        "stopped_at": _timestamp(_value(row, "stopped_at")),
        "failure_reason": _value(row, "failure_reason"),
        "created_at": _timestamp(_value(row, "created_at")),
        "updated_at": _timestamp(_value(row, "updated_at")),
    }
