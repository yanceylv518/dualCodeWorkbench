from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

APPROVAL_ACTIONS = {"delete_file", "install_dependency", "network_access", "git_commit", "git_push"}


@dataclass(frozen=True)
class CredentialRule:
    glob: str
    reason: str


CREDENTIAL_RULES = (
    CredentialRule(".env*", "环境变量文件可能包含访问令牌或密码"),
    CredentialRule("*.pem", "PEM 文件通常包含证书或私钥"),
    CredentialRule("*.key", "KEY 文件通常包含私钥"),
    CredentialRule("*.p12", "PKCS#12 文件可能包含私钥"),
    CredentialRule("*.pfx", "PFX 文件可能包含私钥"),
    CredentialRule("id_rsa*", "OpenSSH RSA 私钥及其派生文件"),
    CredentialRule("id_ed25519*", "OpenSSH Ed25519 私钥及其派生文件"),
    CredentialRule("id_ecdsa*", "OpenSSH ECDSA 私钥及其派生文件"),
    CredentialRule("credentials.json", "标准凭据配置文件"),
    CredentialRule(".npmrc", "npm 配置可能包含仓库令牌"),
    CredentialRule(".netrc", "netrc 文件包含网络服务凭据"),
    CredentialRule("*.keystore", "密钥库文件可能包含签名私钥"),
)


def validate_project_file(path: Path) -> None:
    name = path.name.lower()
    for rule in CREDENTIAL_RULES:
        if fnmatchcase(name, rule.glob):
            raise PermissionError(f"凭据或密钥文件禁止访问：{rule.reason}")


def requires_approval(action: str) -> bool:
    return action in APPROVAL_ACTIONS
