"""A small, dependency-free command line for Virtumon."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
import textwrap
from dataclasses import asdict

from . import __version__
from .agent import AgentClient, AgentError
from .backends.sudovda import DriverError, SudoVDADriver
from .displays import DisplayDiscoveryError, UnsupportedPlatformError, list_displays
from .managed import OperationError, validate_name, validate_request
from .state import StateError

WORDMARK = r""" __     ___      _
 \ \   / (_)_ __| |_ _   _ _ __ ___   ___  _ __
  \ \ / /| | '__| __| | | | '_ ` _ \ / _ \| '_ \
   \ V / | | |  | |_| |_| | | | | | | (_) | | | |
    \_/  |_|_|   \__|\__,_|_| |_| |_|\___/|_| |_|"""
STATUS = (
    "Early development: named Windows virtual monitors with the SudoVDA driver."
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
    print(textwrap.fill("Virtual monitor creation requires SudoVDA. macOS and Linux support is planned.", width=width))
    print()
    for line in (
        "virtumon create work     Add a 1920x1080 virtual monitor",
        "virtumon remove work     Remove that virtual monitor",
        "virtumon list --managed  Show your current virtumons",
        "virtumon list            Show all active Windows displays",
        "virtumon doctor          Check display and driver readiness",
        "virtumon --help          Explore available commands",
    ):
        print(textwrap.fill(line, width=width, subsequent_indent="  "))
    print()


def _list(as_json: bool, managed: bool = False) -> int:
    if managed:
        rows = AgentClient().list_managed()
        if as_json:
            print(json.dumps(rows, indent=2))
        elif not rows:
            print("No virtumons are managed by this user.")
        else:
            print("Virtumons")
            for row in rows:
                print(f"  {row['name']} [{row['status']}]")
                mode = f"{row['width']} x {row['height']} @ {row['refresh_hz']:g} Hz"
                if row["status"] == "active":
                    print(f"    {mode} at ({row['x']}, {row['y']})  {row['device']}")
                else:
                    print(f"    Requested mode: {mode}; live geometry is unverified")
                if row.get("error"):
                    print(f"    {row['error']}")
        return 0
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
    if sys.platform != "win32":
        print("Virtual monitor creation: unsupported on this platform; Windows requires SudoVDA")
        return 2
    try:
        with SudoVDADriver() as driver:
            protocol = driver.protocol()
            timeout, _remaining = driver.watchdog()
            if timeout == 1:
                raise DriverError("SudoVDA's one-second watchdog is too short; restore the upstream three-second default before creating monitors.")
        print("Virtual monitor creation: SudoVDA driver available")
        print(f"Driver protocol: {'.'.join(map(str, protocol[:3]))}; watchdog: {timeout} seconds")
    except DriverError as exc:
        print(f"Virtual monitor creation: unavailable - {exc}")
        print("Driver setup: https://github.com/dependanz/virtumon/blob/feature/windows-virtual-monitors/docs/windows-backend.md")
        result = 1
    try:
        status = AgentClient().status()
        if status["running"]:
            print(f"Agent: running; heartbeat {status['heartbeat']}; {status['managed_count']} managed monitor(s)")
            if status.get("error"):
                print(f"Agent error: {status['error']}")
                result = 1
        else:
            print(f"Agent: stopped or unresponsive; {status['managed_count']} saved monitor(s)")
    except (AgentError, StateError) as exc:
        print(f"Agent: failed - {exc}")
        result = 1
    return result


def _name(value: str) -> str:
    try:
        return validate_name(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _resolution(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([0-9]+)[xX]([0-9]+)", value)
    if match:
        width, height = map(int, match.groups())
        if 1 <= width <= 16384 and 1 <= height <= 16384:
            return width, height
    raise argparse.ArgumentTypeError("Use WIDTHxHEIGHT with each dimension from 1 to 16384 pixels.")


def _create(args) -> int:
    request = {"name": args.name, "width": args.resolution[0], "height": args.resolution[1],
               "refresh_hz": args.refresh, "side": None, "anchor": None}
    for option, side in (("right_of", "right"), ("left_of", "left"), ("above", "above"), ("below", "below")):
        if getattr(args, option) is not None:
            request.update(side=side, anchor=getattr(args, option))
    request = validate_request(request)
    if sys.platform != "win32":
        raise UnsupportedPlatformError("Virtual monitor creation is available on Windows only; macOS and Linux support is planned.")
    row = AgentClient().create(request)
    if args.json:
        print(json.dumps(row, indent=2))
    else:
        print(f"Created {row['name']}: {row['width']} x {row['height']} @ {row['refresh_hz']:g} Hz")
        print(f"  {row['device']} at ({row['x']}, {row['y']})")
        print("The background agent keeps this virtumon alive after the terminal closes.")
    return 0


def _remove(args) -> int:
    if sys.platform != "win32":
        raise UnsupportedPlatformError("Virtual monitor removal is available on Windows only; macOS and Linux support is planned.")
    result = AgentClient().remove(args.names, args.all)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for name in result["removed"]:
            print(f"Removed {name}.")
        if not result["removed"] and not result["errors"]:
            print("No virtumons are managed by this user.")
        for failure in result["errors"]:
            print(f"virtumon: Could not remove {failure['name']}: {failure['error']}", file=sys.stderr)
    return 1 if result["errors"] else 0


def _agent(args) -> int:
    client = AgentClient()
    if args.agent_command == "stop":
        result = client.stop()
        print("The Virtumon agent is already stopped." if result["already_stopped"] else "Stopped the Virtumon agent.")
        return 0
    status = client.status()
    if args.json:
        print(json.dumps(status, indent=2))
    elif status["running"]:
        print(f"Agent: running (Virtumon {status['version']})")
        print(f"Heartbeat: {status['heartbeat']}; managed monitors: {status['managed_count']}")
        if status.get("error"):
            print(status["error"])
    else:
        print(f"Agent: stopped or unresponsive; saved monitors: {status['managed_count']}")
    return 1 if status["running"] and status.get("error") else 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return 0 on success, 1 on failure, 2 for invalid/unsupported use."""
    parser = argparse.ArgumentParser(
        prog="virtumon", description="Your desktop, expanded. " + STATUS,
        epilog="Creation requires an installed SudoVDA driver. Display discovery works independently.",
    )
    parser.add_argument("--version", action="version", version=f"virtumon {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    listing = commands.add_parser("list", help="list active Windows displays (read-only)")
    listing.add_argument("--json", action="store_true", help="emit a JSON array without a banner")
    listing.add_argument("--managed", action="store_true", help="show only this user's Virtumon-owned monitors")
    create = commands.add_parser("create", help="create and verify a named virtual monitor")
    create.add_argument("name", type=_name, help="unique monitor name, for example work")
    create.add_argument("--resolution", type=_resolution, default=(1920, 1080), metavar="WIDTHxHEIGHT")
    create.add_argument("--refresh", type=int, default=60, metavar="HZ", help="whole-number refresh rate (default: 60)")
    placement = create.add_mutually_exclusive_group()
    for option in ("right-of", "left-of", "above", "below"):
        placement.add_argument("--" + option, metavar="ANCHOR", help="place beside 'primary' or an active virtumon name")
    create.add_argument("--json", action="store_true", help="emit verified monitor details as JSON")
    remove = commands.add_parser("remove", help="remove monitors owned by this user")
    remove.add_argument("names", type=_name, nargs="*", help="one or more virtumon names")
    remove.add_argument("--all", action="store_true", help="remove all virtumons owned by this user")
    remove.add_argument("--json", action="store_true", help="emit removed names and any errors as JSON")
    commands.add_parser("doctor", help="check platform, display discovery, driver and agent")
    agent = commands.add_parser("agent", help="inspect or stop the background monitor keeper")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    status = agent_commands.add_parser("status", help="show whether the keeper is running")
    status.add_argument("--json", action="store_true")
    agent_commands.add_parser("stop", help="stop the keeper after all owned monitors have been removed")
    args = parser.parse_args(argv)
    if args.command == "remove" and (args.all and args.names or not args.all and not args.names):
        parser.error("remove requires monitor names or --all, but not both")
    try:
        if args.command == "list":
            return _list(args.json, args.managed)
        if args.command == "create":
            return _create(args)
        if args.command == "remove":
            return _remove(args)
        if args.command == "doctor":
            return _doctor()
        if args.command == "agent":
            return _agent(args)
        _hero()
        return 0
    except ValueError as exc:
        parser.error(str(exc))
    except (AgentError, DriverError, StateError, OperationError, DisplayDiscoveryError, OSError) as exc:
        print(f"virtumon: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, UnsupportedPlatformError) else 1
    except KeyboardInterrupt:
        print("virtumon: Interrupted. An agent operation may still be running; check 'virtumon list --managed'.", file=sys.stderr)
        return 130
