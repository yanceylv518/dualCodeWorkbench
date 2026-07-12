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
