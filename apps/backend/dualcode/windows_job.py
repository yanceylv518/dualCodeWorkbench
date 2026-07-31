"""Small Windows Job Object wrapper used to own Agent subprocess trees."""

from __future__ import annotations

import os


class WindowsProcessJob:
    """Kill every descendant assigned to the job when the handle is closed."""

    def __init__(self, handle: int) -> None:
        self._handle = handle

    @classmethod
    def attach(cls, process_id: int | None) -> WindowsProcessJob | None:
        if os.name != "nt" or not process_id:
            return None
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            kernel32.CloseHandle(job)
            return None
        process = kernel32.OpenProcess(0x0100 | 0x0001, False, process_id)
        if not process:
            kernel32.CloseHandle(job)
            return None
        try:
            if not kernel32.AssignProcessToJobObject(job, process):
                kernel32.CloseHandle(job)
                return None
        finally:
            kernel32.CloseHandle(process)
        return cls(job)

    def close(self) -> None:
        if not self._handle:
            return
        import ctypes

        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._handle)
        self._handle = 0
