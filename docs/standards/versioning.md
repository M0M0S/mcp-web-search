# Versioning Policy

## SemVer

This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html):

```
MAJOR.MINOR.PATCH
```

### Version Rules

| Bump | When |
|------|------|
| **MAJOR** | Incompatible API changes (tool signatures, config schema) |
| **MINOR** | Backward-compatible features (new tools, new providers) |
| **PATCH** | Backward-compatible bug fixes, security patches |

### Current Version

- **Project version** (pyproject.toml): `0.6.0`
- **MCP server version** (runtime): `1.1.2` (`MCP_VERSION` env var)

The MCP server version is independent from the project version — it reflects the MCP protocol interface stability.

## Release Process

1. Update version in `pyproject.toml`
2. Add entry to `CHANGELOG.md` under the new version heading
3. Create a release commit: `chore(release): bump version to X.Y.Z`
4. Tag the release: `git tag -a vX.Y.Z -m "Release X.Y.Z"`
5. Push tag: `git push origin vX.Y.Z`

## Pre-releases

Pre-release versions use the format `X.Y.Z-alpha.N` or `X.Y.Z-beta.N`:

```
0.7.0-alpha.1  # first alpha of 0.7.0
0.7.0-beta.1   # first beta of 0.7.0
```

Pre-releases are for testing and internal use only.

## Deprecation

Deprecated features are marked in `CHANGELOG.md` with a deprecation date and planned removal version. Deprecated features remain functional until the removal version.
