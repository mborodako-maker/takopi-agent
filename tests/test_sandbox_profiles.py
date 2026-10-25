from pathlib import Path
import json
import os
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SECURITY = ROOT / "deploy/security"


def allowed(syscall, arguments=()):
    """Evaluate the profile's rules for the deployed amd64/no-capabilities case."""
    profile = json.loads((SECURITY / "seccomp.json").read_text())
    actions = []
    for rule in profile["syscalls"]:
        if syscall not in rule["names"]:
            continue
        includes = rule.get("includes", {})
        excludes = rule.get("excludes", {})
        if includes.get("caps"):
            continue
        if includes.get("arches") and "amd64" not in includes["arches"]:
            continue
        if "amd64" in excludes.get("arches", []):
            continue
        matches = True
        for argument in rule.get("args", []):
            actual = arguments[argument["index"]]
            value = argument["value"]
            operation = argument["op"]
            if operation == "SCMP_CMP_MASKED_EQ":
                matches &= actual & value == argument.get("valueTwo", 0)
            elif operation == "SCMP_CMP_EQ":
                matches &= actual == value
            elif operation == "SCMP_CMP_NE":
                matches &= actual != value
            elif operation == "SCMP_CMP_LT":
                matches &= actual < value
            elif operation == "SCMP_CMP_LE":
                matches &= actual <= value
            elif operation == "SCMP_CMP_GT":
                matches &= actual > value
            elif operation == "SCMP_CMP_GE":
                matches &= actual >= value
            else:
                raise AssertionError(f"Unhandled seccomp comparison: {operation}")
        if matches:
            actions.append(rule["action"])
    # Explicit errno rules (e.g. clone3 ENOSYS) take priority over ALLOW.
    return bool(actions) and set(actions) == {"SCMP_ACT_ALLOW"}


@pytest.mark.parametrize("flags", [0x10000000, 0x10020000, 0x78020000])
def test_bubblewrap_namespace_flags_are_allowed(flags):
    assert allowed("unshare", [flags])
    assert allowed("clone", [flags | 17])


@pytest.mark.parametrize("flags", [0x02000000, 0x04000000, 0x80, 0x400, 1 << 40])
def test_unneeded_unshare_flags_are_denied(flags):
    assert not allowed("unshare", [flags])


@pytest.mark.parametrize("flags", [0x02000000, 0x04000000])
def test_clone_keeps_cgroup_and_uts_restrictions(flags):
    assert not allowed("clone", [flags | 17])


@pytest.mark.parametrize(
    "name",
    [
        "bpf",
        "keyctl",
        "add_key",
        "request_key",
        "init_module",
        "finit_module",
        "kexec_load",
        "perf_event_open",
        "setns",
        "open_by_handle_at",
        "io_uring_setup",
        "mount_setattr",
        "sethostname",
    ],
)
def test_sensitive_syscalls_still_denied_with_no_capabilities(name):
    assert not allowed(name)


def test_default_deny_and_clone3_fallback_are_preserved():
    profile = json.loads((SECURITY / "seccomp.json").read_text())
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert profile["defaultErrnoRet"] == 1
    clone3 = [rule for rule in profile["syscalls"] if "clone3" in rule["names"]]
    assert any(
        rule["action"] == "SCMP_ACT_ERRNO" and rule["errnoRet"] == 38 for rule in clone3
    )
    assert not allowed("clone3")
    for family in (38, 40):  # AF_ALG, AF_VSOCK
        assert not allowed("socket", [family])
    assert allowed("socket", [2])  # The outer app must reach Telegram/Codex APIs.
    for name in ("mount", "umount2", "pivot_root", "chroot"):
        assert allowed(name)  # The kernel still requires capabilities in that userns.


def test_apparmor_keeps_docker_denials_and_enforces_named_policy():
    profile = (SECURITY / "apparmor.profile").read_text()
    assert "profile takopi-codex flags=(attach_disconnected,mediate_deleted)" in profile
    assert "abi <abi/4.0>," in profile
    for rule in (
        "userns,",
        "mount,",
        "pivot_root,",
        "network unix,",
        "deny network alg,",
        "deny network vsock,",
        "deny @{PROC}/* w,",
        "deny @{PROC}/sysrq-trigger rwklx,",
        "deny @{PROC}/kcore rwklx,",
        "deny /sys/kernel/security/** rwklx,",
        "deny /sys/firmware/** rwklx,",
        "ptrace (trace,tracedby,read,readby) peer=takopi-codex,",
    ):
        assert rule in profile
    assert "flags=(unconfined)" not in profile
    assert "complain" not in profile


def test_overlay_merges_without_adding_capabilities_or_mounts():
    overlay = yaml.safe_load((ROOT / "deploy/compose.sandbox.yaml").read_text())
    assert set(overlay["services"]["takopi"]) == {"security_opt"}
    env = {
        **os.environ,
        "IMAGE_REPOSITORY": "ghcr.io/example/takopi-agent",
        "IMAGE_TAG": "sha-validation",
        "TELEGRAM_BOT_TOKEN": "placeholder",
        "TELEGRAM_ALLOWED_USER_IDS": "123456789",
        "TELEGRAM_CHAT_ID": "123456789",
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "deploy/compose.yaml"),
            "-f",
            str(ROOT / "deploy/compose.sandbox.yaml"),
            "config",
            "--format",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    service = json.loads(result.stdout)["services"]["takopi"]
    assert service["user"] == "1001:1001"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert not service.get("cap_add")
    assert not service.get("privileged")
    options = [value.replace("=", ":", 1) for value in service["security_opt"]]
    assert "no-new-privileges:true" in options
    assert "apparmor:takopi-codex" in options
    assert any(
        value.startswith("seccomp:") and value.endswith("security/seccomp.json")
        for value in options
    )
    assert all("unconfined" not in value for value in options)
    assert len(service["volumes"]) == 5
    assert all(
        mount["target"] not in ("/policyfs", "/host-profiles")
        for mount in service["volumes"]
    )


def test_installer_rejects_unknown_options_before_administrative_actions():
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install-sandbox-profile.sh"), "--typo"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr


def test_loader_requires_an_explicit_trusted_image(tmp_path):
    security = tmp_path / "security"
    security.mkdir()
    (security / "apparmor.profile").write_text("profile takopi-codex {}")
    commands = tmp_path / "bin"
    commands.mkdir()
    parser = commands / "apparmor_parser"
    parser.write_text("#!/bin/sh\nexit 99\n")
    parser.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "TAKOPI_RUNTIME_DIR": str(tmp_path),
    }
    env.pop("TAKOPI_SETUP_IMAGE", None)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install-sandbox-profile.sh"), "--via-docker"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "TAKOPI_SETUP_IMAGE" in result.stderr
    assert result.returncode != 99  # No profile compilation/loading occurred.
