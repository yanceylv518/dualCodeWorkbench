from pathlib import Path

BLOCKED = {".env"}
BLOCKED_SUFFIXES = {".pem", ".key"}
APPROVAL_ACTIONS = {"delete_file", "install_dependency", "network_access", "git_commit", "git_push"}


def validate_project_file(path: Path) -> None:
    if (
        path.name in BLOCKED
        or path.suffix.lower() in BLOCKED_SUFFIXES
        or "id_rsa" in path.name.lower()
    ):
        raise PermissionError("凭据或密钥文件禁止访问")


def requires_approval(action: str) -> bool:
    return action in APPROVAL_ACTIONS
