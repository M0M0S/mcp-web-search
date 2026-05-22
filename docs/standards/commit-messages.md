# Commit Message Format

## Conventional Commits

This project follows [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Type

| Type | When to use |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `style` | Formatting, linting (no code change) |
| `refactor` | Code refactor (no feature/fix) |
| `test` | Adding or updating tests |
| `chore` | Dependencies, build, CI |
| `perf` | Performance improvement |
| `security` | Security-related changes |

### Scope

The scope is the module or area affected:

| Scope | Example |
|-------|---------|
| `search` | Search service |
| `content` | Content extraction |
| `webfetch` | Webfetch agent |
| `config` | Configuration |
| `logging` | Logging setup |
| `cache` | Cache layer |
| `ssrf` | SSRF protection |
| `deps` | Dependencies |

### Description Rules

- Use **imperative mood**: "add" not "added", "fix" not "fixed"
- Keep the first line ≤ **72 characters**
- No period at the end
- Use **English**

### Body (optional)

- Explain **what** and **why** of the change
- Reference external docs or specs if relevant
- Separate from type line with a blank line

### Footer (optional)

| Keyword | Purpose |
|---------|---------|
| `Refs:` | Related issues (non-closing) |
| `Fixes:` | Issues this PR closes |
| `BREAKING CHANGE:` | Incompatible API changes |

## Examples

```
feat(search): add SearxNG as fallback provider

Add SearxNG to the search fallback chain when SEARXNG_BASE is configured.
The provider is inserted between DuckDuckGo and Tavily in the chain.

Refs: #45
```

```
fix(ssrf): block localhost aliases in URL validator

Add 127.0.0.1 and 0.0.0.0 to the private IP blocklist to prevent
SSRF via localhost aliases.

Fixes: #78
```

```
refactor(logging): simplify structlog configuration

Remove custom processors, use default ConsoleRenderer for dev and
JSONFormatter for Docker. Reduces setup complexity.
```

```
chore(deps): bump structlog to 25.1

structlog 25.1 adds improved JSON formatting and Docker compatibility.

Refs: https://github.com/hynek/structlog/releases/tag/v25.1.0
```
