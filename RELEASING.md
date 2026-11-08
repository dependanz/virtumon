# Releasing Virtumon

## Windows management preview

Version **0.2.0a1** on `feature/windows-virtual-monitors` is a candidate for
hardware testing. Build and test the wheel, then retain the output of
`tools/verify_windows.ps1 -Exercise` from a Windows desktop with the reviewed
SudoVDA driver. Check the intended remote-desktop client in that same session.
Unit tests and read-only Windows CI probes do not establish that native monitor
creation works on the target machine.

The [first native Windows acceptance result](docs/windows-acceptance-2026-11-07.md)
passed on commit `fc6e197`: one 1280 x 720 monitor at 60 Hz, background lifetime
beyond the watchdog interval, and removal preserving the original physical
desktop. A separate [native two-monitor acceptance result](docs/windows-acceptance-2026-11-08.md)
passed on experimental commit `8e1ffca`: two independent virtual desktops,
five-second heartbeat, selective removal of `chat` preserving `work`, and clean
removal/agent shutdown. This is a single machine/session verification;
Chrome Remote Desktop interaction, prolonged stability, sleep/reconnect,
and other hardware configurations remain unverified.

Feature-branch and pull-request workflows build distributions and run tests;
they do not publish. Manual publication through the workflow is limited to
`main`; version tags still publish after the build/test jobs pass. Do not create
a release tag for an unvalidated candidate.

## PyPI Trusted Publishing

For the first publication, configure a **pending publisher** at
<https://pypi.org/manage/account/publishing/>:

| Field | Value |
| --- | --- |
| PyPI project name | `virtumon` |
| Owner | `dependanz` |
| Repository | `virtumon` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

The GitHub repository needs an environment named `pypi`. The publishing job
requests a short-lived OpenID Connect token scoped through PyPI's configured
publisher. No PyPI API token belongs in this repository.

After configuration, run **Publish to PyPI** from the Actions page on `main`,
or create a release tag such as `v0.1.0`. The workflow validates the package,
builds the distributions, tests the installed wheel on Windows, macOS, and
Linux, then uploads the verified artifacts to production PyPI.

The first successful upload creates the PyPI project. Merely configuring a
pending publisher does not reserve the project name.

## Manual publication

From an authenticated development environment:

```console
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
python -m twine upload dist/virtumon-0.1.0-py3-none-any.whl dist/virtumon-0.1.0.tar.gz
```

Twine can prompt for authentication or use the platform keyring. Do not place
credentials in a command line, source file, or chat message.

## Version changes

Update both `pyproject.toml` and `src/virtumon/__init__.py`, then update the
changelog. Published filenames cannot be reused on PyPI; fixes to a published
release need a new version.

