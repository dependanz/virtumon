# Releasing Virtumon

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

