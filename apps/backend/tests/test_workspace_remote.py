from pathlib import Path

import pytest

from dualcode.workspace_remote import (
    WorkspaceRemoteSettings,
    WorkspaceRemoteStore,
    derived_repository_path,
    normalize_remote_url,
)


def test_workspace_remote_round_trip(tmp_path: Path):
    store = WorkspaceRemoteStore(tmp_path / "remotes.json")
    value = WorkspaceRemoteSettings(
        remote_url="git@example/repo.git", vps_repo_path="/srv/repos/repo"
    )
    store.save("workspace", value)
    assert store.get("workspace") == value


def test_workspace_remote_rejects_unsafe_path():
    with pytest.raises(ValueError):
        WorkspaceRemoteSettings(vps_repo_path="/srv/repos/../secret")


def test_repository_path_is_derived_from_global_root_and_remote_name():
    assert (
        derived_repository_path(
            "/home/yancey/work", "https://github.com/acme/testDualCode.git", "ignored"
        )
        == "/home/yancey/work/testDualCode"
    )
    assert (
        derived_repository_path("/home/yancey/work", "git@github.com:acme/Orbit.git", "ignored")
        == "/home/yancey/work/Orbit"
    )
    assert derived_repository_path("", "git@github.com:acme/Orbit.git", "ignored") == ""


def test_remote_identity_ignores_transport_case_and_git_suffix():
    assert normalize_remote_url(
        "git@github.com:YanceyLV518/DualCodeWorkBench.git"
    ) == normalize_remote_url("https://github.com/yanceylv518/dualcodeworkbench/")
