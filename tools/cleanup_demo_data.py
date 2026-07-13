"""Remove only known DualCode development/demo workspaces from a stopped database."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DEMO_NAMES = {
    "DualCode Workbench",
    "DualCode Workbench 示例",
    "Agent 联调夹具",
    "ecsMonitor E2E",
    "ecsMonitor Git Handoff",
}


def placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve(strict=True)

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        workspaces = connection.execute(
            f"SELECT id, name, path FROM workspaces WHERE name IN ({placeholders(list(DEMO_NAMES))})",
            list(DEMO_NAMES),
        ).fetchall()
        workspace_ids = [str(item["id"]) for item in workspaces]
        print(f"matched_workspaces={len(workspaces)}")
        for item in workspaces:
            print(f"- {item['name']} | {item['path']}")
        if not args.apply or not workspace_ids:
            print("mode=dry-run" if not args.apply else "mode=apply; nothing to delete")
            return

        workspace_marks = placeholders(workspace_ids)
        thread_ids = [
            str(row[0])
            for row in connection.execute(
                f"SELECT id FROM threads WHERE workspace_id IN ({workspace_marks})", workspace_ids
            ).fetchall()
        ]
        with connection:
            if thread_ids:
                thread_marks = placeholders(thread_ids)
                approval_ids = [
                    str(row[0])
                    for row in connection.execute(
                        f"SELECT id FROM approvals WHERE thread_id IN ({thread_marks})", thread_ids
                    ).fetchall()
                ]
                if approval_ids:
                    connection.execute(
                        f"DELETE FROM execution_jobs WHERE approval_id IN ({placeholders(approval_ids)})",
                        approval_ids,
                    )
                for table in (
                    "messages",
                    "agent_runs",
                    "agent_sessions",
                    "attachments",
                    "file_changes",
                    "test_runs",
                    "approvals",
                ):
                    connection.execute(
                        f"DELETE FROM {table} WHERE thread_id IN ({thread_marks})", thread_ids
                    )
                connection.execute(
                    f"DELETE FROM audit_logs WHERE thread_id IN ({thread_marks})", thread_ids
                )
                connection.execute(
                    f"DELETE FROM threads WHERE id IN ({thread_marks})", thread_ids
                )
            connection.execute(
                f"DELETE FROM audit_logs WHERE workspace_id IN ({workspace_marks})", workspace_ids
            )
            connection.execute(
                f"DELETE FROM workspaces WHERE id IN ({workspace_marks})", workspace_ids
            )
        print(f"deleted_workspaces={len(workspace_ids)}")
        print(f"deleted_threads={len(thread_ids)}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
