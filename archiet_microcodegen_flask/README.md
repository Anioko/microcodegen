# archiet-microcodegen-flask

> PRD text → working Flask + SQLAlchemy app → ZIP, in <1400 LOC, pure stdlib, zero LLM calls.
> Inspired by Karpathy's micrograd: this file is the complete algorithm.

Built on [Archiet](https://archiet.com?utm_source=pypi&utm_medium=package&utm_campaign=microcodegen-flask) — AI-native architecture-to-code platform.

## Install

```bash
pip install archiet-microcodegen-flask
```

## Use

```bash
# Write ZIP to disk
archiet-microcodegen-flask path/to/prd.md --out ./out/

# Pipe ZIP to stdout
archiet-microcodegen-flask path/to/prd.md > app.zip
```

As a library:

```python
from archiet_microcodegen_flask import microcodegen_flask

prd_text = open("prd.md").read()
zip_bytes = microcodegen_flask(prd_text)
```

## What you get

The generated output is a working Flask + PostgreSQL app:

```
app/__init__.py          Flask app factory — create_app()
app/extensions.py        db, jwt, migrate singletons
app/auth/routes.py       register / login / logout / me
app/models/user.py       User model
app/models/<entity>.py   One SQLAlchemy model per entity
app/routes/<entity>.py   One Blueprint per entity (full CRUD)
config.py                JWT_TOKEN_LOCATION=["cookies"], JWT_COOKIE_HTTPONLY=True
manage.py                Flask CLI entrypoint
requirements.txt         flask, flask-sqlalchemy, flask-jwt-extended, flask-migrate, psycopg2-binary
docker-compose.yml       Postgres 16 with healthcheck-gated startup
Dockerfile               python:3.12-slim, port 5000
.env.example             DATABASE_URL, JWT_SECRET_KEY, SECRET_KEY
tests/test_app.py        pytest smoke tests (register, login, health)
ARCHITECTURE.md          ArchiMate 3.2 element map
openapi.yaml             OpenAPI 3.1 spec
GENOME.json              Intermediate representation (for debugging/regeneration)
README.md                Quick-start for the generated app
```

## The four stages

1. **`parse_prd(text) → manifest`** — regex extraction of entities, fields, user stories, integrations
2. **`manifest_to_genome(manifest) → genome`** — maps to canonical IR with ArchiMate 3.2 element typing
3. **`render_genome(genome) → {path: content}`** — `string.Template`-based Flask rendering
4. **`pack(files) → bytes`** — stdlib `zipfile`

## Auth — httpOnly cookies, non-negotiable

- `JWT_TOKEN_LOCATION = ["cookies"]`
- `JWT_COOKIE_HTTPONLY = True`
- `JWT_COOKIE_SAMESITE = "Lax"`
- Register → sets cookie. Login → sets cookie. Logout → clears cookie.
- NEVER localStorage. NEVER Authorization header in response body.

## Why this exists

Spec-driven architecture before vibecoding. The genome is an ArchiMate 3.2
intermediate representation — your PRD becomes an architecture document,
not just a prompt.

The full [Archiet](https://archiet.com?utm_source=pypi&utm_medium=package&utm_campaign=microcodegen-flask) platform adds:

- LLM-powered PRD extraction (chunked, overlap+dedup, handles natural language)
- 12+ stack renderers (FastAPI, Flask, Django, NestJS, Laravel, Go, Java, Rails, .NET, Tauri+Rust, Salesforce, SAP CAP)
- Capability emission, frontend (Next.js / Expo), stub-filling, quality scoring, delivery gates
- Cross-stack parity enforcement and verification

But none of that changes the **core algorithm**. If a bug doesn't reproduce here, it's in an efficiency layer.

## What's NOT included

- No LLM calls (deterministic zone by design)
- No frontend, no mobile, no payment integration, no rate limiting, no audit logging
- No multi-stack output (Flask only; see `archiet-microcodegen` for the FastAPI variant)

## License

MIT. See [LICENSE](https://github.com/aniekanasuquookono-web/archiet/blob/main/LICENSE).

## Links

- **SDD guide:** [github.com/Anioko/spec-driven-development](https://github.com/Anioko/spec-driven-development)
- **Compliance guide:** [github.com/Anioko/compliance-from-architecture](https://github.com/Anioko/compliance-from-architecture) — SOC 2, GDPR, **EU AI Act Annex IV**
- **EU AI Act (deadline Aug 2026):** [Free risk classifier](https://archiet.com/tools/eu-ai-act-risk-classifier) · [Annex IV use case](https://archiet.com/use-cases/eu-ai-act-high-risk-ai-compliance)
- Source: [github.com/aniekanasuquookono-web/archiet](https://github.com/aniekanasuquookono-web/archiet)
- FastAPI variant: [archiet-microcodegen](https://pypi.org/project/archiet-microcodegen/)
- Full platform: [archiet.com](https://archiet.com?utm_source=pypi&utm_medium=package&utm_campaign=microcodegen-flask)
