# Security Policy

## Reporting Security Vulnerabilities

If you discover a security vulnerability in **MCP Web Search**, please report it responsibly:

1. **Do NOT open a public issue** — this could expose the vulnerability to attackers
2. Send a detailed report to the maintainers via private channel (GitHub Security Advisories or direct email)
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will acknowledge receipt within **48 hours** and work to resolve the issue promptly.

## SSRF Protection

This project implements **Server-Side Request Forgery (SSRF)** protection in the `content` tool and all HTTP request paths:

### Protection Layers

1. **Private IP blocklist** — requests to private/reserved IP ranges are rejected:
   - `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
   - `127.0.0.0/8` (localhost)
   - `0.0.0.0`, `169.254.0.0/16` (link-local)
   - `::1` (IPv6 localhost)

2. **URL validation** — only `http://` and `https://` schemes allowed

3. **DNS resolution check** — resolved IP is verified against the blocklist before the request is made

4. **Redirect follow protection** — redirects are validated at each hop against the same rules

### Implementation

SSRF checks are applied in:
- `app/services/content_service.py` — main content extraction path
- `app/core/ssrf.py` — shared HTTP client wrapper
- `app/services/webfetch_service.py` — webfetch agent HTTP paths

## Secret Handling Policy

### Rules

1. **Never commit secrets** — API keys, passwords, tokens must never be in the codebase
2. **Use `.env` files** — all configuration via environment variables (see `.env.example`)
3. **Never log secrets** — sensitive values are masked in logs
4. **`.gitignore` protects** — `.env`, `.venv`, and credential files are excluded from git

### What is NOT a secret

- `.env.example` — template with placeholder values (safe to commit)
- Public API endpoints (DuckDuckGo, SearxNG public instances)
- Open-source library URLs

### What IS a secret

- `LLM_API_KEY` — any LLM provider API key
- `TAVILY_API_KEY` — Tavily search API key
- `GOOGLE_API_KEY`, `GOOGLE_CSE_ID` — Google Custom Search credentials
- `REDIS_URL` — if it contains authentication credentials
- Any other API token or credential

### If a secret is accidentally committed

1. **Rotate the secret immediately** — generate a new key/credential
2. **Do NOT just delete the commit** — the secret remains in git history
3. Use `git filter-branch` or `BFG Repo-Cleaner` to remove the secret from history
4. Report the incident to maintainers

## Dependency Security

- Production dependencies are scanned with **bandit** (`uv run bandit -r app/`)
- Vulnerable dependencies are updated promptly
- `uv.lock` is committed to track exact dependency versions

## Known Security Considerations

| Area | Risk | Mitigation |
|------|------|------------|
| External URL fetching | SSRF | IP blocklist + DNS check + redirect validation |
| LLM API keys | Credential exposure | `.env` only, never logged, `.gitignore` |
| Redis connection | Unauthorized access | `REDIS_URL` in `.env`, network isolation in Docker |
| HTML sanitization | XSS in extracted content | `bleach` library for HTML cleanup |
| User-provided URLs | Malicious payloads | URL scheme validation + SSRF checks |
