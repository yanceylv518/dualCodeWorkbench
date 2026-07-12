from pathlib import Path

import pytest

from dualcode.workspace_remote import WorkspaceRemoteSettings, WorkspaceRemoteStore


def test_workspace_remote_round_trip(tmp_path: Path):
    store = WorkspaceRemoteStore(tmp_path / "remotes.json")
    value = WorkspaceRemoteSettings(remote_url="git@example/repo.git", vps_repo_path="/srv/repos/repo")
    store.save("workspace", value)
    assert store.get("workspace") == value


def test_workspace_remote_rejects_unsafe_path():
    with pytest.raises(ValueError):
        WorkspaceRemoteSettings(vps_repo_path="/srv/repos/../secret")
