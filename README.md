# Virtumon

**Your desktop, expanded.**

Virtumon is the beginning of a friendly toolkit for virtual monitors. The first
release provides a terminal hero banner, Windows display discovery, and a
read-only diagnostic command. It is a small, usable foundation for the display
creation and management features planned next.

## Install

Requires Python 3.10 or newer. Display discovery requires Windows 10 version
1607 or later, or Windows 11.

```console
python -m pip install virtumon
virtumon
```

You can also launch it with `python -m virtumon`.

## Commands

```console
virtumon                 # Show the terminal hero banner and available commands
virtumon --help          # Command-line help
virtumon --version       # Installed version
virtumon list            # List displays in the current Windows desktop
virtumon list --json     # Machine-readable display information
virtumon doctor         # Report platform and display-discovery status
```

Display discovery reports each active display's device name, desktop position,
pixel dimensions, and primary-display status. It reads the current desktop;
it does not install a driver or change display settings. Displays provided by
an existing virtual-display driver can appear alongside physical displays.

## Current support

| Capability | Windows 10/11 | macOS | Linux |
| --- | --- | --- | --- |
| Banner, help, and version | Available | Available | Available |
| Platform diagnostics | Available | Available | Available |
| Native display discovery | Available | Planned | Planned |
| Create or remove virtual monitors | Planned | Planned | Planned |

Version 0.1.0 is an early development release. Virtual monitor creation,
layout profiles, previews, and remote-desktop integration are planned features.
The package has **no runtime dependencies** and makes no network requests.

Color is enabled only for a compatible interactive terminal. Set `NO_COLOR=1`
to request plain output. Narrow terminals use a compact title. JSON output is
kept separate from the banner and diagnostic text.

## Development

```console
python -m pip install -e .
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
python -m unittest discover -s tests -v
```

The release workflow builds a wheel and source distribution, tests the installed
wheel, and publishes through PyPI Trusted Publishing after the publisher is
configured. See [RELEASING.md](https://github.com/dependanz/virtumon/blob/main/RELEASING.md).

## License

MIT. Copyright 2026 Danzel Serrano.

