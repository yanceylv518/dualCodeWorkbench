import json
import os
import re
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, field_validator

from .config import settings


class WorkspaceRemoteSettings(BaseModel):
    remote_url: str = ""
    vps_repo_path: str = ""

    @field_validator("remote_url")
    @classmethod
    def safe_remote(cls, value: str) -> str:
        value = value.strip()
        if any(char in value for char in "\r\n\0"):
            raise ValueError("invalid remote URL")
        return value

    @field_validator("vps_repo_path")
    @classmethod
    def safe_remote_path(cls, value: str) -> str:
        value = value.strip()
        if value:
            path = PurePosixPath(value)
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("VPS repository path must be absolute and normalized")
        return value


class WorkspaceRemoteStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.data_dir / "workspace-remotes.json"

    def _load_all(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, workspace_id: str) -> WorkspaceRemoteSettings:
        return WorkspaceRemoteSettings.model_validate(self._load_all().get(workspace_id, {}))

    def save(self, workspace_id: str, value: WorkspaceRemoteSettings) -> None:
        values = self._load_all()
        values[workspace_id] = value.model_dump()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def remove(self, workspace_id: str) -> None:
        values = self._load_all()
        if workspace_id not in values:
            return
        del values[workspace_id]
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


workspace_remote_store = WorkspaceRemoteStore()


def derived_repository_path(projects_root: str, remote_url: str, workspace_name: str) -> str:
    """Build a stable project path from the configured root and repository identity."""
    if not projects_root:
        return ""
    candidate = remote_url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    candidate = candidate or workspace_name
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip(".-")
    if not safe_name:
        raise ValueError("Unable to derive a safe VPS project directory name")
    return str(PurePosixPath(projects_root) / safe_name)
