from pathlib import Path
import pytest
from dualcode.security import requires_approval, validate_project_file


def test_git_push_requires_approval():
    assert requires_approval("git_push")


@pytest.mark.parametrize(
    "path",
    [
        ".env.production",
        "certificate.pem",
        "signing.key",
        "identity.p12",
        "identity.pfx",
        "id_rsa_backup",
        "id_ed25519",
        "id_ecdsa.pub",
        "config/credentials.json",
        ".npmrc",
        ".netrc",
        "release.keystore",
    ],
)
def test_credential_rules_block_sensitive_files(path: str):
    with pytest.raises(PermissionError):
        validate_project_file(Path(path))


@pytest.mark.parametrize(
    "path",
    [
        "environment.md",
        "certificate.txt",
        "keyboard.json",
        "identity.json",
        "id_report.txt",
        "credentials.schema.json",
        "npmrc.example",
        "netrc.md",
        "keystore.md",
    ],
)
def test_credential_rules_allow_non_sensitive_neighbors(path: str):
    validate_project_file(Path(path))
