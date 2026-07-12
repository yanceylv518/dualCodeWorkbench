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
    claude_ssh_remote_root: str = "/tmp/dualcode-workbench"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir / 'dualcode.db'}"


settings = Settings()
