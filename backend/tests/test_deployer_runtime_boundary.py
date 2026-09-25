from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_compose_isolates_docker_access_and_runtime_secrets_to_the_internal_deployer_boundary():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    deployer = services["deployer"]

    assert "ports" not in deployer
    assert "/var/run/docker.sock:/var/run/docker.sock" in deployer["volumes"]
    assert "checkouts_data:/tmp/dspy-trainer/checkouts:ro" in deployer["volumes"]
    assert deployer["build"]["dockerfile"] == "backend/Deployer.Dockerfile"
    assert (
        "DSPY_TRAINER_DEPLOYER_BACKEND_BASE_IMAGE_ID"
        in deployer["build"]["args"]["BACKEND_BASE_IMAGE"]
    )

    environment = deployer["environment"]
    assert set(environment) == {
        "DSPY_TRAINER_POSTGRES_DSN",
        "DSPY_TRAINER_REDIS_URL",
        "DSPY_TRAINER_QUEUE_NAME",
        "DSPY_TRAINER_ENDPOINT_QUEUE_PREFIX",
        "DSPY_TRAINER_ENDPOINT_INVOCATION_CHANNEL_PREFIX",
        "DSPY_TRAINER_ENDPOINT_WORKER_HEARTBEAT_TTL_SECONDS",
        "DSPY_TRAINER_MLFLOW_TRACKING_URI",
        "DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY",
        "DSPY_TRAINER_DEPLOYER_LEADER_TIMEOUT_SECONDS",
        "DSPY_TRAINER_DEPLOYER_CLAIM_TIMEOUT_SECONDS",
        "DSPY_TRAINER_DEPLOYER_POLL_INTERVAL_SECONDS",
        "DSPY_TRAINER_DEPLOYER_ENDPOINT_READINESS_TIMEOUT_SECONDS",
        "DSPY_TRAINER_DEPLOYER_ENDPOINT_DRAIN_TIMEOUT_SECONDS",
        "DSPY_TRAINER_DEPLOYER_ENDPOINT_RECONCILE_INTERVAL_SECONDS",
        "DSPY_TRAINER_DEPLOYER_IMAGE_RETENTION_COUNT",
        "DSPY_TRAINER_DEPLOYER_BUILD_LOG_MAX_BYTES",
        "DSPY_TRAINER_DEPLOYER_BACKEND_BASE_IMAGE_ID",
        "DSPY_TRAINER_DEPLOYER_IMAGE_REPOSITORY",
        "DSPY_TRAINER_DEPLOYER_PLATFORM_VERSION",
        "DSPY_TRAINER_MANAGED_LABEL_NAMESPACE",
        "DSPY_TRAINER_DEPLOYMENT_ID",
        "DSPY_TRAINER_COMPOSE_PROJECT_NAME",
        "DSPY_TRAINER_COMPOSE_NETWORK_NAME",
        "DSPY_TRAINER_COMPOSE_NETWORK_PROJECT_LABEL",
    }

    for service_name, service in services.items():
        if service_name == "deployer":
            continue
        assert all("docker.sock" not in volume for volume in service.get("volumes", ()))

    assert compose["name"] == "${COMPOSE_PROJECT_NAME:-dspy-trainer}"
    assert (
        compose["networks"]["default"]["name"]
        == "${DSPY_TRAINER_COMPOSE_NETWORK_NAME:-dspy-trainer}"
    )


def test_docker_sdk_is_installed_only_in_the_deployer_image():
    backend_requirements = (
        (ROOT / "backend/requirements.txt").read_text(encoding="utf-8").splitlines()
    )
    deployer_requirements = (
        (ROOT / "backend/deployer-requirements.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert not any(
        requirement.startswith("docker") for requirement in backend_requirements
    )
    assert any(
        requirement.startswith("docker") for requirement in deployer_requirements
    )
