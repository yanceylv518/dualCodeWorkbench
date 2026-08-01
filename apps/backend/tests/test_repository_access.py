import pytest

from dualcode.repository_access import access_result, classify_repository_error, remote_transport


@pytest.mark.parametrize(
    ("detail", "code"),
    [
        ("Permission denied (publickey).", "SSH_KEY_NOT_AUTHORIZED"),
        ("ERROR: Repository not found.", "REPOSITORY_NOT_FOUND"),
        ("Host key verification failed.", "HOST_KEY_VERIFICATION_FAILED"),
        ("fatal: could not read Username", "AUTHENTICATION_REQUIRED"),
        ("Could not resolve host: github.com", "NETWORK_ERROR"),
    ],
)
def test_repository_access_errors_are_actionable(detail: str, code: str) -> None:
    assert classify_repository_error(detail)[0] == code


def test_repository_access_success_does_not_claim_write_access() -> None:
    result = access_result("local", "git@github.com:owner/repo.git", 0)
    assert result.state == "ready"
    assert result.read_access is True
    assert result.write_access == "unknown"
    assert result.transport == "ssh"


def test_remote_transport() -> None:
    assert remote_transport("https://github.com/owner/repo.git") == "https"
    assert remote_transport("git@github.com:owner/repo.git") == "ssh"
