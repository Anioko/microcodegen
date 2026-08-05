# archiet-microcodegen-rails

> PRD text → working Rails 7 API app → ZIP, in <1400 LOC, pure Ruby stdlib, zero LLM calls.  
> Inspired by Karpathy's micrograd: this file is the complete algorithm.

[![Gem Version](https://img.shields.io/gem/v/archiet-microcodegen-rails)](https://rubygems.org/gems/archiet-microcodegen-rails)
[![Ruby](https://img.shields.io/badge/ruby-%3E%3D3.0-CC342D)](https://ruby-lang.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## The fastest path from requirements to a running Rails REST API

You have a PRD (a Markdown file, a Confluence export, a Notion page).  
You want a **Rails 7 API-only app** with real auth, a real database, and real routing — ready to `docker compose up`.  
Most generators give you boilerplate. This gives you *your* app in 3 seconds.

```bash
gem install archiet-microcodegen-rails
archiet-microcodegen-rails prd.md --out ./my-app
cd my-app && cp .env.example .env && docker compose up
```

First request hits `/api/v1/auth/register` before the coffee is done.

---

## Install

```bash
# Global install (recommended)
gem install archiet-microcodegen-rails

# Or run the single Ruby file directly
curl -LO https://raw.githubusercontent.com/aniekanasuquookono-web/archiet/main/archiet_microcodegen_rails/lib/archiet_microcodegen_rails.rb
ruby archiet_microcodegen_rails.rb prd.md --out ./my-app
```

---

## Use

### CLI

```bash
# Write files to a directory
archiet-microcodegen-rails prd.md --out ./my-api

# Write a ZIP instead
archiet-microcodegen-rails prd.md --zip my-api.zip

# Then boot
cd my-api
cp .env.example .env        # edit DATABASE_URL, JWT_SECRET
docker compose up           # Postgres + Puma
```

### Library (Ruby)

```ruby
require 'archiet_microcodegen_rails'

text     = File.read('prd.md')
manifest = parse_prd(text)
genome   = manifest_to_genome(manifest)
files    = render_genome(genome)

# Write to disk
write_disk(files, './output')

# Or get a ZIP blob
zip_bytes = pack_zip(files)
File.binwrite('output.zip', zip_bytes)
```

---

## Sample input: a real PRD excerpt

```markdown
# Task Manager

## Entities

**Project**
  - name: string (required)
  - description: text
  - status: string (required)

**Task**
  - title: string (required)
  - body: text
  - due_date: date
  - priority: string
```

**Output:** a complete Rails 7 API-only app with `Project` and `Task` models, per-tenant
scoping, JWT auth, ActiveRecord, migrations, Dockerfile, and `openapi.yaml` — ready to
`docker compose up`.

---

## What you get

| File | What it does |
|---|---|
| `Gemfile` | Rails 7.2, pg, puma, jwt, bcrypt, rack-cors |
| `config/routes.rb` | `namespace :api do namespace :v1` with `resources` for every entity |
| `config/database.yml` | PostgreSQL, URL-based config from `DATABASE_URL` env var |
| `config/application.rb` | API-only mode, CORS middleware |
| `app/models/user.rb` | `has_secure_password`, email uniqueness validation |
| `app/models/{Entity}.rb` | `belongs_to :user`, `scope :for_user` |
| `app/services/jwt_service.rb` | `JwtService.encode` / `.decode` using `jwt` gem |
| `app/controllers/application_controller.rb` | `before_action :authenticate!`, validates httpOnly cookie |
| `app/controllers/api/v1/auth_controller.rb` | `register`, `login`, `logout`, `me` |
| `app/controllers/api/v1/{Entity}Controller.rb` | Full CRUD, every query scoped to `current_user_id` |
| `db/migrate/*.rb` | One migration per entity + users table |
| `.env.example` | All required env vars pre-documented |
| `Dockerfile` | Multi-stage Ruby 3.3 Alpine build |
| `docker-compose.yml` | App + Postgres 16, healthcheck-gated |
| `ARCHITECTURE.md` | ArchiMate 3.2 ApplicationComponent + DataObject inventory |
| `openapi.yaml` | Machine-readable API contract |

---

## The four stages

```
parse_prd(text)              → manifest   (entities, stories, integrations)
manifest_to_genome(manifest) → genome     (ArchiMate 3.2 typed IR)
render_genome(genome)        → files      (Rails 7 Ruby source)
pack_zip(files) / write_disk(files, dir)
```

**Stage 1** — regex-based PRD parser. Finds entities, fields, user stories, and
third-party integrations (Stripe, SendGrid, Twilio, …) without an LLM.

**Stage 2** — converts the manifest into a structured genome. Every entity gains
`id`, `user_id`, `created_at`, `updated_at` automatically. The genome is a plain Ruby
Hash — no classes, no magic, no external gems.

**Stage 3** — renders all Rails files from the genome. Auth uses `bcrypt`
(`has_secure_password`) and a `JwtService` backed by the `jwt` gem — standard Rails
practice, no custom crypto in generated code.

**Stage 4** — writes files to a directory or produces a valid PKZIP file using
`Zlib::Deflate.new(level, -15)` (raw deflate, zero header overhead) and Ruby's
`Array#pack`. No shell calls, no external gems.

---

## Security by default

- **httpOnly cookie, not localStorage.** The `access_token` cookie is `httponly: true`,
  `same_site: :lax`. The JWT payload never touches JavaScript.
- **Per-tenant isolation.** Every model has `scope :for_user, ->(uid) { where(user_id: uid) }`.
  Every controller calls `.for_user(current_user_id)` before any query. There is no code
  path that returns another user's data.
- **Zero hardcoded secrets.** `JWT_SECRET` and `DATABASE_URL` are environment variables.
  `SECRET_KEY_BASE` is required at boot — the generated `.env.example` documents all of them.

---

## archiet-microcodegen-rails vs the alternatives

| | `archiet-microcodegen-rails` | `rails new --api` | `rails g scaffold` |
|---|---|---|---|
| Input | Your PRD | Nothing | Single model name |
| Output | Full CRUD API for all entities | Empty skeleton | One resource at a time |
| Auth | JWT httpOnly cookie + bcrypt | None | None |
| Per-tenant isolation | Built-in (`for_user` scope) | None | None |
| `docker-compose.yml` | ✅ | ❌ | ❌ |
| `openapi.yaml` | ✅ | ❌ | ❌ |
| `ARCHITECTURE.md` | ✅ ArchiMate 3.2 | ❌ | ❌ |
| LLM / API key | ❌ Never | ❌ | ❌ |

---

## FAQ

**Does the generated app really boot with `docker compose up`?**  
Yes. The generated `Dockerfile` uses a multi-stage Ruby 3.3 Alpine build.
`docker-compose.yml` waits for Postgres `pg_isready` before starting Puma.

**Is the generator itself pure Ruby stdlib?**  
The generator uses only `zlib`, `fileutils`, `json`, `optparse`, and `stringio` — all
part of the Ruby standard library. No Bundler dependency for the generator itself.
The generated app has its own `Gemfile`.

**What Rails version does it generate?**  
Rails 7.2, API-only mode, with `Rack::Cors` for CORS handling.

**Does it use Devise or Doorkeeper for auth?**  
No. The generated app uses Rails `has_secure_password` (bcrypt) and a small `JwtService`
backed by the `jwt` gem. One less configuration surface. If you prefer Devise, the
generator is a single Ruby file — fork and adapt Stage 3.

**What's NOT generated?**  
Action Mailer, Active Job, Action Cable, Turbo, Hotwire, front-end scaffolding,
multi-tenancy with separate schemas, and Rails credentials. For a full-stack app generated
from your architecture diagram, see
[archiet.com](https://archiet.com?utm_source=rubygems&utm_medium=package&utm_campaign=microcodegen-rails).

---

## Why this exists

Architecture before code. A vibe-coded Rails app has models and routes.
An *architected* Rails app has a formal representation of why those models and routes
exist — what requirement they satisfy, what component they belong to, what boundaries
they must not cross.

`archiet-microcodegen-rails` encodes that representation as an ArchiMate 3.2 genome
and renders it deterministically. Same PRD → same app. No hallucinations.

The genome is not a prompt. It is a typed intermediate representation: every entity has
an archimate type, every field has a domain type, every auth rule is a structural
constraint — not a comment in a template.

For teams that want a full architecture-to-code platform (multi-stack, governance, PRD
intake, quality scoring, delivery gates), visit
[archiet.com](https://archiet.com?utm_source=rubygems&utm_medium=package&utm_campaign=microcodegen-rails).

---

## Links

- **SDD guide:** [github.com/Anioko/spec-driven-development](https://github.com/Anioko/spec-driven-development)
- **Compliance guide:** [github.com/Anioko/compliance-from-architecture](https://github.com/Anioko/compliance-from-architecture) — SOC 2, GDPR, **EU AI Act Annex IV**
- **EU AI Act (deadline Aug 2026):** [Free risk classifier](https://archiet.com/tools/eu-ai-act-risk-classifier) · [Annex IV use case](https://archiet.com/use-cases/eu-ai-act-high-risk-ai-compliance)
- Full platform: [archiet.com](https://archiet.com?utm_source=rubygems&utm_medium=package&utm_campaign=microcodegen-rails)

*Generated with [archiet-microcodegen-rails](https://rubygems.org/gems/archiet-microcodegen-rails)*
