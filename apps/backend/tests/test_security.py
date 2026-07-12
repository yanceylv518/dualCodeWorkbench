from pathlib import Path
import pytest
from dualcode.security import requires_approval, validate_project_file


def test_git_push_requires_approval():
    assert requires_approval("git_push")


def test_secret_is_blocked():
    with pytest.raises(PermissionError):
        validate_project_file(Path(".env"))
