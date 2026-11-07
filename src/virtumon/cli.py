"""A small, dependency-free command line for Virtumon."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import textwrap
from dataclasses import asdict

from . import __version__
from .displays import DisplayDiscoveryError, UnsupportedPlatformError, list_displays

WORDMARK = r""" __     ___      _
 \ \   / (_)_ __| |_ _   _ _ __ ___   ___  _ __
  \ \ / /| | '__| __| | | | '_ ` _ \ / _ \| '_ \
   \ V / | | |  | |_| |_| | | | | | | (_) | | | |
    \_/  |_|_|   \__|\__,_|_| |_| |_|\___/|_| |_|"""
STATUS = (
    "Early development: display discovery is available; "
    "virtual monitor creation is coming."
)


def _color_enabled() -> bool:
    if not sys.stdout.isatty() or "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return False
    if sys.platform == "win32":
        # Enable ANSI on modern Windows consoles; quietly use ASCII otherwise.
        import ctypes
        import msvcrt
        from ctypes import wintypes

        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = msvcrt.get_osfhandle(sys.stdout.fileno())
            mode = wintypes.DWORD()
            kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetConsoleMode.restype = wintypes.BOOL
            kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.SetConsoleMode.restype = wintypes.BOOL
            return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode))
                        and kernel32.SetConsoleMode(handle, mode.value | 4))
        except (OSError, ValueError, AttributeError):
            return False
    return True


def _hero() -> None:
    width = max(20, shutil.get_terminal_size(fallback=(80, 24)).columns)
    color = _color_enabled()

    def paint(value: str, code: str) -> str:
        return f"\033[{code}m{value}\033[0m" if color else value

    print()
    if width >= max(map(len, WORDMARK.splitlines())) + 2:
        for index, line in enumerate(WORDMARK.splitlines()):
            print(paint(line, "96" if index < 3 else "95"))
    else:
        print(paint("V I R T U M O N", "96"))
    print(paint(f"  v{__version__}  |  Your desktop, expanded.", "95") if width >= 44
          else f"v{__version__}\n" + textwrap.fill("Your desktop, expanded.", width=width))
    print()
    print(textwrap.fill(STATUS, width=width))
    print(textwrap.fill("Windows display discovery is available now. macOS and Linux support is planned.", width=width))
    print()
    for line in (
        "virtumon list         Show active Windows displays",
        "virtumon list --json  Machine-readable display geometry",
        "virtumon doctor       Check platform and display discovery",
        "virtumon --help       Explore available commands",
    ):
        print(textwrap.fill(line, width=width, subsequent_indent="  "))
    print()


def _list(as_json: bool) -> int:
    try:
        displays = list_displays()
    except DisplayDiscoveryError as exc:
        # Keep stdout empty on failure, including when --json was requested.
        print(f"virtumon: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, UnsupportedPlatformError) else 1
    if as_json:
        print(json.dumps([asdict(display) for display in displays], indent=2))
    elif not displays:
        print("No active displays were found in this desktop session.")
    else:
        print("Active displays (physical pixels)")
        for display in displays:
            primary = " [primary]" if display.primary else ""
            print(f"  {display.device}{primary}")
            print(f"    {display.width} x {display.height} at ({display.x}, {display.y})")
    return 0


def _doctor() -> int:
    print(f"Virtumon {__version__}")
    print(f"Platform: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Python: {platform.python_version()}")
    try:
        displays = list_displays()
    except UnsupportedPlatformError as exc:
        print(f"Display discovery: unsupported - {exc}")
        result = 2
    except DisplayDiscoveryError as exc:
        print(f"Display discovery: failed - {exc}")
        result = 1
    else:
        print(f"Display discovery: OK ({len(displays)} active display(s))")
        result = 0
    print("Virtual monitor creation: not implemented in this release")
    return result


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return 0 on success, 1 on discovery failure, 2 if unsupported."""
    parser = argparse.ArgumentParser(
        prog="virtumon", description="Your desktop, expanded. " + STATUS,
        epilog="Display discovery supports Windows 10 (1607+) and Windows 11.",
    )
    parser.add_argument("--version", action="version", version=f"virtumon {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    listing = commands.add_parser("list", help="list active Windows displays (read-only)")
    listing.add_argument("--json", action="store_true", help="emit a JSON array without a banner")
    commands.add_parser("doctor", help="check platform, Python and display discovery")
    args = parser.parse_args(argv)
    if args.command == "list":
        return _list(args.json)
    if args.command == "doctor":
        return _doctor()
    _hero()
    return 0
