import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from .config import settings


class AgentSettings(BaseModel):
    enable_real_agents: bool = True
    codex_executable: str = "codex"
    codex_model: str = ""
    codex_reasoning_effort: str = "medium"
    claude_executable: str = "claude"
    claude_model: str = "opus"
    claude_reasoning_effort: str = "medium"
    claude_ssh_enabled: bool = False
    claude_ssh_host: str = ""
    claude_ssh_username: str = ""
    claude_ssh_port: int = Field(default=22, ge=1, le=65535)
    claude_ssh_known_hosts: str = ""
    claude_ssh_client_key: str = ""
    claude_ssh_remote_root: str = "/tmp/dualcode-workbench"
    claude_ssh_executable: str = "/usr/local/bin/claude"
    test_executable: str = ""
    test_arguments: list[str] = Field(default_factory=lambda: ["-m", "pytest", "-q"])

    @field_validator("claude_ssh_host", "claude_ssh_username")
    @classmethod
    def no_whitespace(cls, value: str) -> str:
        if any(char.isspace() for char in value):
            raise ValueError("must not contain whitespace")
        return value

    @field_validator("codex_model", "claude_model")
    @classmethod
    def safe_model_name(cls, value: str) -> str:
        value = value.strip()
        if value and (len(value) > 100 or not all(char.isalnum() or char in "._-:/" for char in value)):
            raise ValueError("model name contains unsupported characters")
        return value

    @field_validator("codex_reasoning_effort", "claude_reasoning_effort")
    @classmethod
    def valid_reasoning_effort(cls, value: str) -> str:
        if value not in {"low", "medium", "high", "xhigh", "max", "ultra"}:
            raise ValueError("unsupported reasoning effort")
        return value

    @field_validator("claude_ssh_remote_root", "claude_ssh_executable")
    @classmethod
    def absolute_remote_root(cls, value: str) -> str:
        if not value.startswith("/") or ".." in value.split("/"):
            raise ValueError("remote root must be an absolute normalized path")
        return value

    def validate_local_paths(self) -> None:
        if (
            self.enable_real_agents
            and self.test_executable
            and not Path(self.test_executable).is_file()
        ):
            raise ValueError("test executable path does not exist")
        if self.claude_ssh_enabled:
            if not self.claude_ssh_host or not self.claude_ssh_username:
                raise ValueError("SSH host and username are required")
            if not self.claude_ssh_known_hosts or not Path(self.claude_ssh_known_hosts).is_file():
                raise ValueError("A valid known_hosts file is required")
            if self.claude_ssh_client_key and not Path(self.claude_ssh_client_key).is_file():
                raise ValueError("SSH client key path does not exist")


class AgentSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.data_dir / "agent-settings.json"

    def load(self) -> AgentSettings:
        if not self.path.exists():
            return AgentSettings(
                enable_real_agents=settings.enable_real_agents,
                codex_executable=settings.codex_executable,
                claude_executable=settings.claude_executable,
                claude_ssh_enabled=bool(settings.claude_ssh_host),
                claude_ssh_host=settings.claude_ssh_host or "",
                claude_ssh_username=settings.claude_ssh_username or "",
                claude_ssh_port=settings.claude_ssh_port,
                claude_ssh_known_hosts=str(settings.claude_ssh_known_hosts or ""),
                claude_ssh_client_key=str(settings.claude_ssh_client_key or ""),
                claude_ssh_remote_root=settings.claude_ssh_remote_root,
            )
        return AgentSettings.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, value: AgentSettings) -> None:
        value.validate_local_paths()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)


agent_settings_store = AgentSettingsStore()
