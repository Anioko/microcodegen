# microcodegen.py

**PRD text → production Flask app → ZIP bytes. One Python file, zero dependencies.**

Inspired by Andrej Karpathy's [micrograd](https://github.com/karpathy/micrograd) — this is the core Archiet algorithm in its simplest form.

```bash
python microcodegen.py your-prd.md > app.zip
unzip app.zip -d myapp && cd myapp && pip install -r requirements.txt && flask run
```

→ A **working, bootable Flask app** at `http://localhost:5000`.

---

## What it generates from your PRD

Given a plain-English Product Requirements Document, `microcodegen.py` outputs a complete Flask application:

- **SQLAlchemy models** — one per entity extracted from your PRD
- **JWT auth** — `/auth/register`, `/auth/login`, `/auth/me` with httpOnly cookies
- **CRUD API routes** — full REST for every entity
- **Per-tenant data isolation** — every query scoped to the authenticated user
- **Alembic migrations** — `flask db upgrade` and you're running
- **pytest test suite** — auth + entity CRUD tests, all passing
- **docker-compose.yml** — Postgres + app, one command local dev

Zero LLM calls. Zero API keys. Pure Python stdlib.

---

## Run it

```bash
# Python 3.10+
git clone https://github.com/Anioko/microcodegen
cd microcodegen

# Generate from an example PRD
python microcodegen.py examples/task_manager.md --out ./my-task-app
cd my-task-app
pip install -r requirements.txt
flask db upgrade
flask run
# → http://localhost:5000/auth/register
```

Or pipe the ZIP:

```bash
python microcodegen.py examples/task_manager.md > app.zip
```

---

## Write your own PRD

```markdown
# Project Tracker

## Entities
- Project: name (string, required), description (text), status (string)
- Task: title (string, required), completed (boolean), due_date (datetime)
- Comment: body (text, required)

## User Stories
- As a developer, I want to manage projects so that I can track my work.
- As a developer, I want to add tasks to projects so that I can break work into steps.
```

Save as `prd.md`, run:

```bash
python microcodegen.py prd.md --out ./my-app
```

---

## The four stages

```
PRD text  (your requirements document)
   │
   ▼  Stage 1: parse_prd(text) → manifest
   │  Pure regex extraction — entities, fields, user stories, integrations.
   │  No LLM. Misses subtle PRDs; that's acceptable for a spec reference.
   │
   ▼  Stage 2: manifest_to_genome(manifest) → genome
   │  Converts the manifest into a stack-neutral architectural genome dict.
   │  Key decisions: Flask, PostgreSQL, JWT, per-user isolation.
   │
   ▼  Stage 3: render_genome(genome) → {path: content}
   │  string.Template substitution over embedded Flask templates.
   │  Outputs SQLAlchemy models, Blueprints, Alembic env, pytest suite.
   │
   ▼  Stage 4: pack(files) → ZIP bytes
      stdlib zipfile.ZipFile. Download it, push it to GitHub, deploy it.
```

---

## Why one file?

The same philosophy as [micrograd](https://github.com/karpathy/micrograd) and [minGPT](https://github.com/karpathy/minGPT):

> *If you can't express the core algorithm in a single file, you're hiding behind layers.*

`microcodegen.py` serves three purposes:
1. **Onboarding** — a new engineer understands what Archiet *is* in 10 minutes
2. **Regression check** — a bug that doesn't repro here is in the efficiency layers, not the algorithm
3. **Spec** — any algorithmic change (new genome key, new manifest field) updates this file first

---

## What this file does NOT include

This is the minimum viable PRD→code pipeline. Production use cases need more:

| Feature | Where it lives |
|---|---|
| LLM-powered PRD extraction (handles natural language) | [archiet.com](https://archiet.com) |
| **9 backend stacks**: NestJS, Django, FastAPI, Go (chi), Java Spring Boot, .NET, Laravel, Rails | [archiet.com](https://archiet.com) |
| React/Next.js frontend (shadcn/ui) | [archiet.com](https://archiet.com) |
| Expo mobile app (iOS + Android) | [archiet.com](https://archiet.com) |
| Compliance artifact packs — SOC 2, HIPAA, GDPR, PCI-DSS control matrices | [archiet.com](https://archiet.com) |
| 13-gate shippability audit (security scan, import coherence, boot test) | [archiet.com](https://archiet.com) |
| GitHub push + drift detection + architecture scoring | [archiet.com](https://archiet.com) |
| Quality score ≥ 80 gate (delivery blocked on broken output) | [archiet.com](https://archiet.com) |

**[Free plan at archiet.com](https://archiet.com)** — 1 full app per month, no credit card.

---

## Architecture deep dive

### Stage 1 — `parse_prd(text) → manifest`

Pure regex extraction across four pattern classes:

- `_ENTITY_PATTERN` — finds entity section headers (`## Entities`, `## Data Models`)
- `_ENTITY_NAME_PATTERN` — finds entity names as list items or sub-headers
- `_FIELD_PATTERN` — finds `- fieldname: type (modifiers)` declarations
- `_USER_STORY_PATTERN` — finds `As a X, I want Y so that Z.` sentences

The full Archiet pipeline replaces this with a chunked LLM extractor (overlap + dedup merge) that handles PRDs that don't follow a rigid format. The algorithm shape is identical — only the extraction quality changes.

### Stage 2 — `manifest_to_genome(manifest) → genome`

Converts the manifest into a formal **architectural genome** — the stack-neutral intermediate representation that drives all downstream rendering.

The genome encodes:
- `language` — `flask-nextjs` (this file's scope; the full system supports 9 stacks)
- `modules[]` — one module per entity, with entities, user_stories, acceptance_criteria
- `capabilities[]` — inferred from the PRD (auth, payments if Stripe mentioned, etc.)
- `delivery_archetype` — product class, tenant model, auth model

This is the IR that makes multi-stack generation possible. The same genome dict drives Flask, NestJS, Django, FastAPI, Go, Java, .NET, Laravel, and Rails renderers — the stacks are just different `render_genome()` implementations.

### Stage 3 — `render_genome(genome) → {path: content}`

`string.Template` substitution across templates embedded directly in the file. Each template is a complete source file with `$variable` placeholders.

Templates emitted:
- `config.py` — Flask app factory, SQLAlchemy, JWT, migrations config
- `models/user.py` — User model with password hash
- `models/<entity>.py` — SQLAlchemy model per entity
- `blueprints/auth_bp.py` — JWT register/login/logout/me
- `blueprints/<entity>_bp.py` — CRUD Blueprint per entity
- `alembic/env.py` — migration environment
- `tests/test_api.py` — pytest suite with auth fixtures
- `docker-compose.yml` — Postgres + app service
- `requirements.txt`

The full Archiet renderer uses Jinja2 with a 1,200+ template library. The `string.Template` approach here is deliberately simple to keep the algorithm readable.

### Stage 4 — `pack(files) → bytes`

`zipfile.ZipFile(DEFLATED)`. Maps `{path: content}` → ZIP bytes. The customer downloads this ZIP and runs it.

---

## Examples

See [`examples/`](examples/) for sample PRDs:

- [`task_manager.md`](examples/task_manager.md) — project/task management SaaS
- [`saas_billing.md`](examples/saas_billing.md) — subscription billing with Stripe
- [`ecommerce.md`](examples/ecommerce.md) — product catalog + orders

---

## Contributing

The algorithm is intentionally simple. Before adding features, ask:

> *"Is this part of how a PRD becomes shippable code? Or is it tuning, fallback, or quality?"*

If the former, open a PR. If the latter, it belongs in the full Archiet pipeline.

---

## License

MIT. Use it, fork it, learn from it.

The production platform (9 stacks, compliance packs, quality gates, GitHub push) is commercial: **[archiet.com](https://archiet.com)**
