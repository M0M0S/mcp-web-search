# Branching Strategy

## Branch Naming Convention

```
<type>/<short-description>
```

### Types

| Type | Example | When to use |
|------|---------|-------------|
| `feat` | `feat/searxng-fallback` | New feature development |
| `fix` | `fix/ssrf-localhost` | Bug fixes |
| `docs` | `docs/contributing-guide` | Documentation changes |
| `style` | `style/format-code` | Formatting only (no code change) |
| `refactor` | `refactor/logging-setup` | Code restructuring |
| `test` | `test/webfetch-judge` | Test additions/updates |
| `chore` | `chore/update-deps` | Dependencies, build, CI |
| `perf` | `perf/cache-warming` | Performance improvements |
| `security` | `security/ssrf-hardening` | Security-related changes |

### Rules

- Use **kebab-case** (lowercase with hyphens)
- Keep names short and descriptive (≤ 40 characters)
- One branch per logical change
- Do NOT use `main` or `master` for work — always create a feature branch

## Merge Strategy

| PR Type | Strategy | Reason |
|---------|----------|--------|
| Feature branches | **Squash merge** | Keeps history clean, one commit per feature |
| Small fixes | **Rebase merge** | Preserves individual fix commits |
| Documentation | **Squash merge** | Doc changes are typically small |
| Chore/deps | **Squash merge** | Dependency updates are single-purpose |

## Branch Lifecycle

1. Create branch from `main`
2. Work on the branch
3. Rebase onto `main` before merging (keep up-to-date)
4. Submit PR
5. Merge (squash or rebase)
6. Delete the branch

## main Branch Protection

- Direct pushes to `main` are prohibited
- All changes must go through PRs
- PRs require at least one review
- CI must pass before merging
