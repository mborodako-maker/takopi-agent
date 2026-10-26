from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(name):
    # BaseLoader preserves the GitHub Actions "on" key as a string.
    return yaml.load((ROOT / name).read_text(), Loader=yaml.BaseLoader)


def test_production_mounts_and_security():
    service = load_yaml("deploy/compose.yaml")["services"]["takopi"]
    assert "build" not in service
    assert service["image"] == (
        "${IMAGE_REPOSITORY:?Set IMAGE_REPOSITORY to ghcr.io/owner/takopi-agent}:"
        "${IMAGE_TAG:?Set an explicit IMAGE_TAG}"
    )
    assert service["user"] == "1001:1001"
    assert service["read_only"] == "true"
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "privileged" not in service
    assert "network_mode" not in service
    assert "devices" not in service
    assert service["pids_limit"] == "256"
    assert "mem_limit" in service and "cpus" in service
    mounts = service["volumes"]
    assert {mount["target"]: mount["source"] for mount in mounts} == {
        "/workspace": "./workspace",
        "/home/codex/.takopi": "./data/takopi",
        "/home/codex/.takopi/takopi.toml": "./config/takopi.toml",
        "/home/codex/.codex": "./data/codex",
        "/var/log/takopi": "./data/logs",
    }
    assert all(mount["type"] == "bind" for mount in mounts)
    assert all(mount["bind"]["create_host_path"] == "false" for mount in mounts)
    config = next(m for m in mounts if m["source"] == "./config/takopi.toml")
    assert config["read_only"] == "true"
    env = service["environment"]
    assert "TELEGRAM_ALLOWED_USER_IDS:?" in env["TELEGRAM_ALLOWED_USER_IDS"]
    assert "USER 1001:1001" in (ROOT / "docker/Dockerfile").read_text()


def test_local_build_is_separate():
    dev = load_yaml("compose.dev.yaml")["services"]["takopi"]
    assert dev["build"] == {"context": ".", "dockerfile": "docker/Dockerfile"}
    assert load_yaml("docker-compose.yml")["include"] == ["compose.dev.yaml"]


def test_publish_requires_full_checks_and_scoped_registry_permissions():
    workflow = load_yaml(".github/workflows/docker.yml")
    triggers = workflow["on"]
    assert triggers["push"] == {"branches": ["master", "main"], "tags": ["v*"]}
    assert "workflow_dispatch" in triggers
    assert "pull_request" in triggers
    assert workflow["permissions"] == {"contents": "read"}
    tests = workflow["jobs"]["tests"]["steps"]
    assert any(step.get("run") == "uv sync --frozen" for step in tests)
    assert any(step.get("run") == "just check" for step in tests)
    assert "uv run pytest" in (ROOT / "Justfile").read_text()
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == "tests"
    assert publish["if"] == "github.event_name != 'pull_request'"
    assert publish["permissions"] == {"contents": "read", "packages": "write"}
    steps = publish["steps"]
    assert any(
        s.get("uses", "").startswith("docker/setup-buildx-action@") for s in steps
    )
    login = next(
        s for s in steps if s.get("uses", "").startswith("docker/login-action@")
    )
    assert login["with"]["registry"] == "ghcr.io"
    assert login["with"]["password"] == "${{ secrets.GITHUB_TOKEN }}"
    meta = next(s for s in steps if s.get("id") == "meta")["with"]
    assert "type=sha,prefix=sha-" in meta["tags"]
    assert "type=ref,event=tag" in meta["tags"]
    assert (
        "type=raw,value=latest,enable=${{ github.ref == 'refs/heads/master' || github.ref == 'refs/heads/main' }}"
        in meta["tags"]
    )
    assert meta["flavor"] == "latest=false"
    build = next(
        s for s in steps if s.get("uses", "").startswith("docker/build-push-action@")
    )
    assert build["with"]["push"] == "true"
    assert build["with"]["cache-from"] == "type=gha"
    assert build["with"]["cache-to"] == "type=gha,mode=max"
    assert "ssh" not in (ROOT / ".github/workflows/docker.yml").read_text().lower()


@pytest.mark.parametrize("entrypoint", ["update-vps.sh", "update.sh"])
@pytest.mark.parametrize("fail_pull", [False, True])
def test_vps_update_order_and_fail_fast(tmp_path, entrypoint, fail_pull):
    runtime = tmp_path / "runtime with spaces"
    runtime.mkdir()
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "docker.log"
    docker = commands / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "%s|%s\\n" "$PWD" "$*" >> "$CALL_LOG"\n'
        'if [ "$*" = "compose pull" ] && [ "$FAIL_PULL" = "1" ]; then exit 23; fi\n'
    )
    docker.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / entrypoint)],
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "TAKOPI_RUNTIME_DIR": str(runtime),
            "CALL_LOG": str(log),
            "FAIL_PULL": "1" if fail_pull else "0",
        },
        capture_output=True,
        check=False,
    )
    assert result.returncode == (23 if fail_pull else 0)
    expected = ["compose pull"]
    if not fail_pull:
        expected += ["compose up -d --no-build", "compose ps"]
    assert log.read_text().splitlines() == [f"{runtime}|{cmd}" for cmd in expected]


@pytest.mark.parametrize("tag", ["v0.1.0", "sha-abc1234"])
def test_compose_resolves_explicit_image_without_build(tag):
    if shutil.which("docker") is None:
        pytest.fail("Docker Compose is required for deployment validation")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(ROOT / "deploy/.env.example"),
            "-f",
            str(ROOT / "deploy/compose.yaml"),
            "config",
            "--format",
            "json",
        ],
        env={
            **os.environ,
            "IMAGE_REPOSITORY": "ghcr.io/example/takopi-agent",
            "IMAGE_TAG": tag,
            "TELEGRAM_BOT_TOKEN": "placeholder",
            "TELEGRAM_ALLOWED_USER_IDS": "123456789",
            "TELEGRAM_CHAT_ID": "123456789",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    service = json.loads(result.stdout)["services"]["takopi"]
    assert service["image"] == f"ghcr.io/example/takopi-agent:{tag}"
    assert "build" not in service


@pytest.mark.parametrize(
    "missing", ["IMAGE_REPOSITORY", "IMAGE_TAG", "TELEGRAM_ALLOWED_USER_IDS"]
)
def test_production_refuses_missing_required_settings(missing):
    env = {
        **os.environ,
        "IMAGE_REPOSITORY": "ghcr.io/example/takopi-agent",
        "IMAGE_TAG": "v0.1.0",
        "TELEGRAM_BOT_TOKEN": "placeholder",
        "TELEGRAM_ALLOWED_USER_IDS": "123456789",
        "TELEGRAM_CHAT_ID": "123456789",
    }
    env[missing] = ""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "deploy/compose.yaml"),
            "config",
            "--quiet",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert missing in result.stderr
