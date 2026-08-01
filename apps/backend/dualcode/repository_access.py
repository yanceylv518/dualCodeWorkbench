from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


AccessState = Literal["unconfigured", "ready", "action_required", "error", "unavailable"]


class RepositoryAccessResult(BaseModel):
    environment: Literal["local", "vps"]
    state: AccessState
    transport: Literal["ssh", "https", "unknown"] = "unknown"
    read_access: bool = False
    write_access: Literal["unknown"] = "unknown"
    error_code: str = ""
    summary: str = ""


def remote_transport(remote_url: str) -> Literal["ssh", "https", "unknown"]:
    value = remote_url.strip().lower()
    if value.startswith("git@") or value.startswith("ssh://"):
        return "ssh"
    if value.startswith("https://") or value.startswith("http://"):
        return "https"
    return "unknown"


def classify_repository_error(detail: str) -> tuple[str, str]:
    value = detail.lower()
    if "host key verification failed" in value:
        return "HOST_KEY_VERIFICATION_FAILED", "SSH 主机指纹校验失败，请检查 known_hosts 配置。"
    if "permission denied (publickey)" in value:
        return "SSH_KEY_NOT_AUTHORIZED", "当前 SSH 公钥尚未获得该仓库访问权限。"
    if "repository not found" in value or "repository does not exist" in value:
        return "REPOSITORY_NOT_FOUND", "仓库不存在，或当前账号无权查看该仓库。"
    if any(
        marker in value
        for marker in (
            "authentication failed",
            "could not read username",
            "terminal prompts disabled",
            "authentication required",
        )
    ):
        return "AUTHENTICATION_REQUIRED", "仓库需要身份验证，请配置可访问该仓库的凭据。"
    if any(
        marker in value
        for marker in (
            "could not resolve host",
            "name or service not known",
            "connection timed out",
            "connection refused",
            "network is unreachable",
        )
    ):
        return "NETWORK_ERROR", "无法连接 Git 服务，请检查网络、域名和防火墙。"
    return "UNKNOWN", "仓库访问检测失败，请查看运行日志中的原始错误。"


def access_result(
    environment: Literal["local", "vps"], remote_url: str, returncode: int, detail: str = ""
) -> RepositoryAccessResult:
    transport = remote_transport(remote_url)
    if returncode == 0:
        return RepositoryAccessResult(
            environment=environment,
            state="ready",
            transport=transport,
            read_access=True,
            summary="已验证仓库读取权限；写入权限将在首次推送时确认。",
        )
    code, summary = classify_repository_error(detail)
    return RepositoryAccessResult(
        environment=environment,
        state="action_required"
        if code in {"SSH_KEY_NOT_AUTHORIZED", "AUTHENTICATION_REQUIRED", "REPOSITORY_NOT_FOUND"}
        else "error",
        transport=transport,
        error_code=code,
        summary=summary,
    )
