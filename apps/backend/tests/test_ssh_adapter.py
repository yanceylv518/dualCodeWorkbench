from pathlib import Path, PurePosixPath

import pytest

from dualcode.adapters import AgentRequest
from dualcode.ssh_adapter import ClaudeSshAdapter, ClaudeSshConfig


def config(tmp_path: Path, **changes):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("host ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest\n")
    values = {
        "host": "claude.example",
        "username": "dualcode",
        "known_hosts": known_hosts,
    }
    values.update(changes)
    return ClaudeSshConfig(**values)


def test_requires_known_hosts(tmp_path: Path):
    with pytest.raises(ValueError, match="known_hosts"):
        ClaudeSshAdapter(config(tmp_path, known_hosts=tmp_path / "missing"))


def test_rejects_relative_remote_root(tmp_path: Path):
    with pytest.raises(ValueError, match="remote_root"):
        ClaudeSshAdapter(config(tmp_path, remote_root=PurePosixPath("relative")))


def test_remote_thread_directory_uses_validated_uuid(tmp_path: Path):
    adapter = ClaudeSshAdapter(config(tmp_path))
    with pytest.raises(ValueError, match="UUID"):
        adapter._remote_dir(AgentRequest("../escape", "prompt", {}), "run")
