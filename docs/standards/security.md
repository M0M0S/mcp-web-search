# Security Standards

## SSRF Protection

### Overview

All external URL fetching in this project is protected against **Server-Side Request Forgery (SSRF)** attacks. Protection is applied at multiple layers.

### Protection Layers

#### 1. URL Scheme Validation

Only `http://` and `https://` schemes are allowed. All other schemes are rejected:

- `file://`, `ftp://`, `gopher://`, `javascript://`, `data://` — blocked
- Empty or malformed URLs — blocked

#### 2. Private IP Blocklist

The following IP ranges are blocked:

| Range | Type |
|-------|------|
| `10.0.0.0/8` | Private (Class A) |
| `172.16.0.0/12` | Private (Class B) |
| `192.168.0.0/16` | Private (Class C) |
| `127.0.0.0/8` | Loopback (localhost) |
| `0.0.0.0` | Unspecified |
| `169.254.0.0/16` | Link-local |
| `::1` | IPv6 loopback |
| `fe80::/10` | IPv6 link-local |

#### 3. DNS Resolution Check

Before making an HTTP request:

1. Resolve the hostname to an IP address
2. Check the resolved IP against the private IP blocklist
3. Reject if the IP falls within a blocked range

#### 4. Redirect Validation

Each redirect hop is validated against the same rules:

- Scheme must be `http` or `https`
- Resolved IP must not be in the private blocklist
- Maximum redirect depth enforced (configurable)

### Implementation Locations

| File | Protection Applied |
|------|-------------------|
| `app/services/content_service.py` | Content extraction URL validation |
| `app/core/ssrf.py` | Shared HTTP client wrapper |
| `app/services/webfetch_service.py` | Agent HTTP paths |
| `app/core/url_validator.py` | Core URL validation logic |

## Secret Handling

### Rules

1. **Environment variables only** — all secrets via `.env` (see `.env.example`)
2. **Never in code** — API keys, passwords, tokens must never be hardcoded
3. **Never in logs** — sensitive values are masked before logging
4. **`.gitignore` protection** — `.env`, `.venv`, credentials excluded from git

### Secret Categories

| Category | Variables | Example |
|----------|-----------|---------|
| LLM API | `LLM_API_KEY`, `LLM_BASE_URL` | OpenAI, Anthropic keys |
| Search API | `TAVILY_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_CSE_ID` | Tavily, Google CSE |
| Redis | `REDIS_URL` (with auth) | Redis credentials |

### Template Files

`.env.example` contains placeholder values and is safe to commit. It serves as a configuration reference.

## HTML Sanitization

Extracted HTML content is sanitized using **bleach** to prevent XSS:

- All tags not in the allowlist are stripped
- Attributes are validated against the allowlist
- URLs in attributes are scheme-validated

## Dependency Security

- Production dependencies scanned with **bandit** (`uv run bandit -r app/`)
- Vulnerable dependencies updated promptly
- `uv.lock` committed for exact version tracking
