from pathlib import Path

import pytest

from dualcode.workspace_remote import WorkspaceRemoteSettings, WorkspaceRemoteStore, derived_repository_path


def test_workspace_remote_round_trip(tmp_path: Path):
    store = WorkspaceRemoteStore(tmp_path / "remotes.json")
    value = WorkspaceRemoteSettings(remote_url="git@example/repo.git", vps_repo_path="/srv/repos/repo")
    store.save("workspace", value)
    assert store.get("workspace") == value


def test_workspace_remote_rejects_unsafe_path():
    with pytest.raises(ValueError):
        WorkspaceRemoteSettings(vps_repo_path="/srv/repos/../secret")


def test_repository_path_is_derived_from_global_root_and_remote_name():
    assert derived_repository_path("/home/yancey/work", "https://github.com/acme/testDualCode.git", "ignored") == "/home/yancey/work/testDualCode"
    assert derived_repository_path("/home/yancey/work", "git@github.com:acme/Orbit.git", "ignored") == "/home/yancey/work/Orbit"
    assert derived_repository_path("", "git@github.com:acme/Orbit.git", "ignored") == ""
