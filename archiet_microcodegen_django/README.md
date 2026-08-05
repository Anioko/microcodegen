# archiet-microcodegen-django

> PRD text → working Django REST Framework app → ZIP, in <1400 LOC, pure stdlib, zero LLM calls.
> Inspired by Karpathy's micrograd: this file is the complete algorithm.

[![PyPI](https://img.shields.io/pypi/v/archiet-microcodegen-django)](https://pypi.org/project/archiet-microcodegen-django/)

## Install

```bash
pip install archiet-microcodegen-django
```

## Use

```bash
# Write ZIP to stdout
archiet-microcodegen-django path/to/prd.md > app.zip

# Extract directly to a directory
archiet-microcodegen-django path/to/prd.md --out ./out/
```

As a library:

```python
from archiet_microcodegen_django import microcodegen_django

prd_text = open("prd.md").read()
zip_bytes = microcodegen_django(prd_text)
open("app.zip", "wb").write(zip_bytes)
```

## What you get

The generated ZIP is a working Django 5 + PostgreSQL app with:

| File | Purpose |
|------|---------|
| `manage.py` | Standard Django management entry point |
| `<project>/settings.py` | DATABASES, INSTALLED_APPS, REST_FRAMEWORK, SIMPLE_JWT |
| `<project>/urls.py` | DefaultRouter + auth endpoints wired |
| `<project>/wsgi.py` | Gunicorn-compatible WSGI application |
| `apps/accounts/models.py` | Custom User model (UUID PK, email login) |
| `apps/accounts/authentication.py` | `JWTCookieAuthentication` — reads httpOnly cookie |
| `apps/accounts/views.py` | register / login / logout / me / health |
| `apps/<entity>/models.py` | Django Model with `user = ForeignKey(User)` for tenant isolation |
| `apps/<entity>/serializers.py` | `ModelSerializer` with all fields |
| `apps/<entity>/views.py` | `ModelViewSet` with `get_queryset` filtered by `request.user` |
| `apps/<entity>/urls.py` | `DefaultRouter` registration |
| `requirements.txt` | Django, DRF, simplejwt, psycopg2-binary, gunicorn, PyJWT, django-cors-headers |
| `Dockerfile` | Python 3.12-slim, gunicorn production server |
| `docker-compose.yml` | Postgres 16 with healthcheck-gated startup |
| `.env.example` | Pre-populated with generated secrets |
| `ARCHITECTURE.md` | ArchiMate 3.2 element map |
| `openapi.yaml` | OpenAPI 3.1 spec — importable into Postman / Swagger UI |
| `GENOME.json` | The intermediate representation that drove rendering |

## Auth design (non-negotiable)

JWT is set as an **httpOnly, SameSite=Lax cookie** — never in the response body, never in localStorage. This prevents XSS token theft without requiring a custom Authorization header.

- `POST /api/auth/register` → sets cookie, returns `{user: {id, email}}`
- `POST /api/auth/login` → sets cookie, returns `{user: {id, email}}`
- `POST /api/auth/logout` → deletes cookie
- `GET /api/auth/me` → returns current user (cookie required)

## The four stages

1. **`parse_prd(text) → manifest`** — regex extraction of entities, fields, user stories, integrations (verbatim from `scripts/microcodegen.py`)
2. **`manifest_to_genome(manifest) → genome`** — maps to the canonical ArchiMate 3.2-typed IR (verbatim from `scripts/microcodegen.py`)
3. **`render_genome(genome) → {path: content}`** — Django DRF rendering (this package's contribution)
4. **`pack(files) → bytes`** — stdlib `zipfile` (verbatim from `scripts/microcodegen.py`)

## Per-tenant data isolation

Every generated Model has a `user = ForeignKey(User, on_delete=CASCADE)` field. Every ViewSet overrides `get_queryset` to filter by `request.user`. Cross-user data access is structurally impossible via the generated API.

## Quick start (generated app)

```bash
cp .env.example .env
docker compose up
curl http://localhost:8000/health/

# Register
curl -c cookies.txt -X POST http://localhost:8000/api/auth/register \
     -H "Content-Type: application/json" \
     -d '{"email":"you@example.com","password":"hunter22hunter"}'

# Create an entity
curl -b cookies.txt -X POST http://localhost:8000/api/items/ \
     -H "Content-Type: application/json" \
     -d '{"name":"My first item"}'
```

## Why this exists

Spec-driven architecture before vibecoding. The genome is an ArchiMate 3.2 intermediate representation — your PRD becomes an architecture document, not just a prompt.

The full [Archiet](https://archiet.com?utm_source=pypi&utm_medium=package&utm_campaign=microcodegen-django) platform adds:

- LLM-powered PRD extraction (chunked, overlap+dedup)
- 12+ stack renderers (FastAPI, Django, NestJS, Go, Java, Rails, .NET, Tauri+Rust, Salesforce, SAP CAP, Dynamics, Laravel)
- Frontend (Next.js / Expo), quality scoring, delivery gates, formal ArchiMate 3.2 models

But none of that changes the **core algorithm**. If a bug doesn't reproduce here, it's in an efficiency layer.

## Links

- **SDD guide:** [github.com/Anioko/spec-driven-development](https://github.com/Anioko/spec-driven-development)
- **Compliance guide:** [github.com/Anioko/compliance-from-architecture](https://github.com/Anioko/compliance-from-architecture) — SOC 2, GDPR, **EU AI Act Annex IV**
- **EU AI Act (deadline Aug 2026):** [Free risk classifier](https://archiet.com/tools/eu-ai-act-risk-classifier) · [Annex IV use case](https://archiet.com/use-cases/eu-ai-act-high-risk-ai-compliance)
- Full platform: [archiet.com](https://archiet.com?utm_source=pypi&utm_medium=package&utm_campaign=microcodegen-django)

## License

MIT.
