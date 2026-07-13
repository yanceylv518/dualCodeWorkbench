"""Frozen entry point used by the Tauri desktop sidecar."""

import multiprocessing
import argparse
import ctypes
import os
import sys
import threading

import uvicorn
from dualcode.main import app


def monitor_parent(parent_pid: int) -> None:
    """Exit if the Tauri parent is terminated without a graceful shutdown."""
    synchronize = 0x00100000
    infinite = 0xFFFFFFFF
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(synchronize, False, parent_pid)
    if not handle:
        # A missing/inaccessible parent must never leave a detached backend behind.
        os._exit(0)
    kernel32.WaitForSingleObject(handle, infinite)
    kernel32.CloseHandle(handle)
    os._exit(0)


def main() -> None:
    multiprocessing.freeze_support()
    # PyInstaller windowed executables expose no standard streams. Alembic and
    # uvicorn both expect writable streams during startup, even when logs are
    # intentionally hidden by the desktop application.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    if args.parent_pid:
        threading.Thread(target=monitor_parent, args=(args.parent_pid,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8876, log_level="info")


if __name__ == "__main__":
    main()
