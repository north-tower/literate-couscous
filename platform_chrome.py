# -*- coding: utf-8 -*-
"""Chrome path and process helpers for Sendline worker (Windows + macOS)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def find_chrome_exe() -> Path:
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        for path in candidates:
            if path.is_file():
                return path
        raise FileNotFoundError("Could not find Google Chrome (chrome.exe)")

    if sys.platform == "darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
        for path in candidates:
            if path.is_file():
                return path
        raise FileNotFoundError("Could not find Google Chrome.app")

    raise FileNotFoundError(f"Unsupported platform for Chrome lookup: {sys.platform}")


def pids_on_port(port: int) -> list[int]:
    if sys.platform == "win32":
        return _pids_on_port_netstat(port)
    if sys.platform == "darwin":
        return _pids_on_port_lsof(port)
    return _pids_on_port_lsof(port)


def _pids_on_port_netstat(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []
    pids: list[int] = []
    needle = f":{port}"
    for line in (result.stdout or "").splitlines():
        if "LISTEN" not in line.upper():
            continue
        if needle not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid and pid not in pids:
            pids.append(pid)
    return pids


def _pids_on_port_lsof(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid and pid not in pids:
            pids.append(pid)
    return pids


def _child_pids(parent: int) -> list[int]:
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(parent)],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return pids


def kill_pid_tree(pid: int | None) -> None:
    if not pid:
        return
    root = int(pid)
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(root), "/T"],
            capture_output=True,
            text=True,
            check=False,
        )
        return

    if sys.platform == "darwin":
        to_kill: list[int] = []
        stack = [root]
        seen: set[int] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            to_kill.append(current)
            stack.extend(_child_pids(current))
        for p in to_kill:
            try:
                os.kill(p, 9)
            except OSError:
                pass
        return

    try:
        os.kill(root, 15)
    except OSError:
        pass
