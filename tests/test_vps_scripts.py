from pathlib import Path
import os

import anyio
import pytest


@pytest.mark.anyio
@pytest.mark.parametrize("route", ["direct", "parent", "symlink", "checkout_alias"])
async def test_backup_rejects_resolved_checkout_destination(tmp_path, route):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"
    script = scripts / "backup.sh"
    script.write_text(source.read_text())
    destination = checkout / "workspace" / "backups"
    if route == "parent":
        destination = tmp_path / ".." / tmp_path.name / "checkout" / "workspace"
    elif route == "symlink":
        link = tmp_path / "outside-link"
        link.symlink_to(checkout, target_is_directory=True)
        destination = link / "backups"
    if route == "checkout_alias":
        alias = tmp_path / "checkout-alias"
        alias.symlink_to(checkout, target_is_directory=True)
        script = alias / "scripts" / "backup.sh"
    result = await anyio.run_process(
        ["bash", str(script)],
        env={**os.environ, "BACKUP_DIR": str(destination)},
        check=False,
    )
    assert result.returncode != 0
    assert b"Backup must be outside the checkout" in result.stderr
    assert not destination.exists()
