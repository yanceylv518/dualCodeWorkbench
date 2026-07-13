import os
import secrets
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    data_dir: Path = Path.home() / ".dualcode-workbench"
    max_attachment_bytes: int = 10 * 1024 * 1024
    allowed_attachment_types: tuple[str, ...] = (
        "image/png",
        "image/jpeg",
        "image/webp",
        "text/plain",
    )
    enable_real_agents: bool = True
    codex_executable: str = "codex"
    claude_executable: str = "claude"
    claude_ssh_host: str | None = None
    claude_ssh_username: str | None = None
    claude_ssh_port: int = 22
    claude_ssh_known_hosts: Path | None = None
    claude_ssh_client_key: Path | None = None
    claude_ssh_remote_root: str = ""

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir / 'dualcode.db'}"


settings = Settings()


def load_sidecar_token() -> str:
    configured = os.environ.get("DUALCODE_SIDECAR_TOKEN")
    if configured:
        return configured
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    token_path = settings.data_dir / "sidecar.token"
    temporary = token_path.with_suffix(".tmp")
    try:
        temporary.write_text(token, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(token_path)
        token_path.chmod(0o600)
    except OSError:
        # Sandboxed/test processes can still authenticate in-memory; production and the
        # documented browser-dev directory are writable by the current user.
        pass
    return token


sidecar_token = load_sidecar_token()
