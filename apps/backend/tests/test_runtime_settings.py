from pathlib import Path

import pytest

from dualcode.runtime_settings import AgentSettings, AgentSettingsStore


def test_settings_round_trip_without_secret_material(tmp_path: Path):
    store = AgentSettingsStore(tmp_path / "agent-settings.json")
    value = AgentSettings(codex_executable="C:/tools/codex.exe")
    store.save(value)
    assert store.load() == value
    assert not (tmp_path / "agent-settings.tmp").exists()


def test_ssh_settings_require_known_hosts(tmp_path: Path):
    store = AgentSettingsStore(tmp_path / "agent-settings.json")
    value = AgentSettings(
        claude_ssh_enabled=True,
        claude_ssh_host="vps.example",
        claude_ssh_username="dualcode",
        claude_ssh_known_hosts=str(tmp_path / "missing"),
    )
    with pytest.raises(ValueError, match="known_hosts"):
        store.save(value)


def test_remote_runtime_root_is_derived_from_ssh_user():
    assert AgentSettings(claude_ssh_username="yancey").claude_remote_root == "/home/yancey/.dualcode"
    assert AgentSettings(claude_ssh_username="root").claude_remote_root == "/root/.dualcode"
    assert AgentSettings(claude_ssh_username="yancey", claude_ssh_remote_root="/srv/dualcode/").claude_remote_root == "/srv/dualcode"
    assert AgentSettings(claude_ssh_projects_root="/home/yancey/work/").claude_ssh_projects_root == "/home/yancey/work"


def test_legacy_shared_tmp_root_migrates_to_automatic_user_root(tmp_path: Path):
    path = tmp_path / "agent-settings.json"
    legacy = AgentSettings(
        claude_ssh_username="yancey",
        claude_ssh_remote_root="/tmp/dualcode-workbench",
    )
    path.write_text(legacy.model_dump_json(), encoding="utf-8")
    loaded = AgentSettingsStore(path).load()
    assert loaded.claude_ssh_remote_root == ""
    assert loaded.claude_remote_root == "/home/yancey/.dualcode"
