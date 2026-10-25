"""Run account-free enforcement checks inside the production Takopi container."""

from pathlib import Path
import json
import os
import socket
import subprocess
import tempfile


def main() -> None:
    status = Path("/proc/self/status").read_text()
    assert os.getuid() == 1001, "Expected the application UID 1001"
    assert "CapEff:\t0000000000000000" in status, "Unexpected capabilities"
    assert "NoNewPrivs:\t1" in status, "no-new-privileges is missing"
    assert "Seccomp:\t2" in status, "Seccomp filter is missing"
    assert (
        Path("/proc/self/attr/current").read_text().strip() == "takopi-codex (enforce)"
    )
    # The outer process can create sockets, so inner denial is not an outer
    # network outage. No external connection is attempted.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM):
        pass
    with tempfile.TemporaryDirectory(
        prefix=".takopi-sandbox-check-", dir="/workspace"
    ) as temporary:
        directory = Path(temporary)
        project = directory / "project"
        project.mkdir()
        outside = directory / "outside"
        outside.mkdir()
        subprocess.run(
            ["git", "-c", "init.templateDir=", "init", "-q", str(project)], check=True
        )
        paths = [
            str(outside / "forbidden.txt"),
            "/home/codex/.codex/.sandbox-forbidden.txt",
        ]
        program = f"""
from pathlib import Path
import errno, json, os, socket
allowed = Path({str(project / "allowed.txt")!r})
allowed.write_text('sandbox workspace write verified')
assert os.getuid() == 1001
status = Path('/proc/self/status').read_text()
assert 'CapEff:\\t0000000000000000' in status
assert 'NoNewPrivs:\\t1' in status
assert 'Seccomp:\\t2' in status
blocked = []
for target in {paths!r}:
    try:
        Path(target).write_text('THIS WRITE MUST BE DENIED')
    except OSError as error:
        assert error.errno in (errno.EROFS, errno.EACCES, errno.EPERM), (target, error)
        blocked.append(target)
    else:
        raise AssertionError('Write outside project succeeded: ' + target)
try:
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
except OSError as error:
    assert error.errno in (errno.EACCES, errno.EPERM), error
else:
    connection.close()
    raise AssertionError('Sandbox permitted an IP socket with network_access=false')
print(json.dumps({{'workspace_write': True, 'outside_writes_denied': len(blocked), 'network_denied': True, 'uid': os.getuid(), 'capabilities': 0}}))
"""
        result = subprocess.run(
            [
                "codex",
                "sandbox",
                "-c",
                'sandbox_mode="workspace-write"',
                "-c",
                "sandbox_workspace_write.network_access=false",
                "--",
                "python",
                "-c",
                program,
            ],
            cwd=project,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError(
                f"Sandbox enforcement check failed ({result.returncode}): {result.stdout}\n{result.stderr}"
            )
        checks = json.loads(result.stdout.strip())
        assert checks["outside_writes_denied"] == 2
        assert (
            project / "allowed.txt"
        ).read_text() == "sandbox workspace write verified"
        assert not (outside / "forbidden.txt").exists()
        assert not Path(paths[1]).exists()
        print(json.dumps(checks, sort_keys=True))
    print(
        "PASS: production container restrictions and Codex filesystem/network sandbox enforced"
    )


if __name__ == "__main__":
    main()
