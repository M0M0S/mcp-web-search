# Contributing to MCP Web Search

Thank you for your interest in contributing to **MCP Web Search**. This document outlines the process and standards for contributing to this project.

## How to Contribute

### 1. Fork and Clone

```bash
git clone https://github.com/M0M0S/mcp-web-search.git
cd mcp-web-search
uv sync
```

### 2. Create a Branch

Follow the [branch naming convention](#branch-naming-convention).

### 3. Make Changes

- Write code that follows the project standards (see below)
- Add or update tests for any changes
- Update documentation if needed

### 4. Run Checks Before Submitting

```bash
# Linting
uv run ruff check app/ tests/

# Type checking
uv run mypy app/

# Tests
uv run pytest tests/ -v

# Coverage (optional)
uv run pytest tests/ --cov=app --cov-report=term-missing
```

All checks must pass before submitting a PR.

### 5. Submit a Pull Request

- Use a descriptive title following [conventional commits format](#commit-message-format)
- Describe the changes in the PR body
- Link any related issues
- Ensure CI passes

## Standards

### Code Quality

| Tool | Command | Purpose |
|------|---------|---------|
| **ruff** | `uv run ruff check app/` | Linting, formatting |
| **mypy** | `uv run mypy app/` | Static type checking |
| **pytest** | `uv run pytest tests/` | Unit & integration tests |
| **bandit** | `uv run bandit -r app/` | Security scan (dev only) |

### Python Version

Project requires **Python ≥ 3.12**. All code must be compatible with this version.

### Dependency Management

Use **uv** for all dependency operations:

```bash
uv add <package>      # add a dependency
uv remove <package>   # remove a dependency
uv sync               # sync lock file
```

Do not manually edit `uv.lock`.

## Commit Message Format

Follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Types

| Type | When to use |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only changes |
| `style` | Formatting, linting (no code change) |
| `refactor` | Code refactor (no feature or fix) |
| `test` | Adding or updating tests |
| `chore` | Dependency updates, build config, CI |
| `perf` | Performance improvement |
| `security` | Security-related changes |

### Rules

- Use **imperative mood** in the description ("add" not "added")
- Keep the first line ≤ 72 characters
- Use **English** for commit messages
- Reference issues in the footer: `Refs: #123`

### Examples

```
feat(search): add SearxNG as fallback provider

Add SearxNG to the search fallback chain when configured.

Refs: #45
```

```
fix(content): SSRF check for localhost aliases

Add 127.0.0.1 and 0.0.0.0 to the private IP blocklist.

Fixes: #78
```

## Branch Naming Convention

```
<type>/<short-description>
```

### Types

| Type | Example |
|------|---------|
| `feat` | `feat/searxng-fallback` |
| `fix` | `fix/ssrf-localhost` |
| `docs` | `docs/contributing-guide` |
| `refactor` | `refactor/logging-setup` |
| `test` | `test/webfetch-judge` |
| `chore` | `chore/update-deps` |
| `security` | `security/ssrf-hardening` |

### Rules

- Use **kebab-case**
- Keep names short and descriptive
- One branch per logical change

## Merge Strategy

- **Squash merge** for feature branches (keeps history clean)
- **Rebase merge** for small fixes (preserves individual commits)
- All PRs require at least one review before merging
- CI must pass before merging

## Code of Conduct

- Be respectful and constructive in reviews
- Focus on the code, not the person
- Explain "why" in feedback, not just "what"

## Questions?

Open an issue or reach out to the maintainers.
