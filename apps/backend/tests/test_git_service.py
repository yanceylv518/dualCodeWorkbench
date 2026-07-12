import uuid
from pathlib import Path

import pytest

from dualcode.git_service import GitService


def test_managed_worktree_path_cannot_escape(tmp_path: Path):
    service = GitService(tmp_path / "managed")
    workspace_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    target = service.worktree_path(workspace_id, thread_id)
    assert service.managed_root in target.parents


def test_branch_name_rejects_untrusted_input(tmp_path: Path):
    service = GitService(tmp_path / "managed")
    with pytest.raises(ValueError):
        service.branch_name("../../main")
