# archiet-microcodegen-laravel

> PRD text → working Laravel 11 app → ZIP, in <1400 LOC, pure PHP stdlib, zero LLM calls.  
> Inspired by Karpathy's micrograd: this file is the complete algorithm.

[![Packagist](https://img.shields.io/packagist/v/archiet/microcodegen-laravel)](https://packagist.org/packages/archiet/microcodegen-laravel)
[![PHP](https://img.shields.io/badge/php-%3E%3D8.2-8892BF)](https://php.net)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## The fastest path from requirements to a running Laravel REST API

You have a PRD (a Markdown file, a Confluence export, a Notion page).  
You want a **Laravel 11 REST API** with real auth, a real database, and real routing — ready to `docker compose up`.  
Most tools give you a prompt and a prayer. This gives you a ZIP in 3 seconds.

```bash
composer global require archiet/microcodegen-laravel
archiet-microcodegen-laravel prd.md --out ./my-app
cd my-app && cp .env.example .env && docker compose up
```

First request hits `/api/auth/register` before the coffee is done.

---

## Install

```bash
# Global install (recommended)
composer global require archiet/microcodegen-laravel

# Or run the single PHP file directly
curl -LO https://raw.githubusercontent.com/aniekanasuquookono-web/archiet/main/archiet_microcodegen_laravel/bin/archiet-microcodegen-laravel.php
php archiet-microcodegen-laravel.php prd.md --out ./my-app
```

---

## Use

### CLI

```bash
# Write files to a directory
archiet-microcodegen-laravel prd.md --out ./my-api

# Write a ZIP instead
archiet-microcodegen-laravel prd.md --zip my-api.zip

# Then boot
cd my-api
cp .env.example .env        # edit DB_PASSWORD, JWT_SECRET
docker compose up           # Postgres + Laravel artisan serve
php artisan migrate         # (runs automatically on first boot via Dockerfile)
```

### Library (PHP)

```php
require 'vendor/autoload.php';

$text     = file_get_contents('prd.md');
$manifest = parse_prd($text);
$genome   = manifest_to_genome($manifest);
$files    = render_genome($genome);

// Write to disk
write_disk($files, './output');

// Or get a ZIP blob
$zip = zip_create($files);
file_put_contents('output.zip', $zip);
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

**Output:** a complete Laravel 11 app with `Project` and `Task` models, per-tenant
scoping, JWT auth, Eloquent ORM, migrations, Dockerfile, and `openapi.yaml` — ready to
`docker compose up`.

---

## What you get

| File | What it does |
|---|---|
| `composer.json` | Package manifest, declares `laravel/framework ^11.0` |
| `artisan` | Laravel CLI entry point |
| `bootstrap/app.php` | Laravel 11 bootstrap with API routing and JWT middleware alias |
| `config/database.php` | PostgreSQL config driven by `DATABASE_URL` env var |
| `config/jwt.php` | JWT secret + TTL from env |
| `routes/api.php` | Auth routes + `apiResource` for every entity |
| `app/Models/User.php` | `Authenticatable`, `fillable`, `hidden` |
| `app/Models/{Entity}.php` | Eloquent model with `scopeForUser(Builder $q, int $uid)` |
| `app/Http/Controllers/AuthController.php` | `register`, `login`, `logout`, `me` with inline JWT helpers |
| `app/Http/Controllers/{Entity}Controller.php` | Full CRUD, every query scoped to `_user_id` |
| `app/Http/Middleware/JwtMiddleware.php` | Validates `access_token` httpOnly cookie |
| `database/migrations/*.php` | One migration per entity + users table |
| `.env.example` | All required env vars pre-documented |
| `Dockerfile` | Multi-stage PHP 8.3 build |
| `docker-compose.yml` | App + Postgres 16, healthcheck-gated |
| `ARCHITECTURE.md` | ArchiMate 3.2 ApplicationComponent + DataObject inventory |
| `openapi.yaml` | Machine-readable API contract |

---

## The four stages

```
parse_prd(text)              → manifest   (entities, stories, integrations)
manifest_to_genome(manifest) → genome     (ArchiMate 3.2 typed IR)
render_genome(genome)        → files      (Laravel 11 PHP source)
zip_create(files) / write_disk(files, dir)
```

**Stage 1** — regex-based PRD parser. Finds entities, fields, user stories, and
third-party integrations (Stripe, SendGrid, Twilio, …) without an LLM.

**Stage 2** — converts the manifest into a structured genome. Every entity gains
`id`, `user_id`, `created_at`, `updated_at` automatically. The genome is a plain PHP
array — no classes, no ORM, no magic.

**Stage 3** — renders all Laravel files from the genome. Auth logic (JWT encode/decode,
httpOnly cookie) is baked into `AuthController.php` as plain functions — no Sanctum, no
Passport, no external library required at runtime.

**Stage 4** — writes files to a directory or produces a valid PKZIP file using PHP's
native `gzdeflate()` and `pack()`. Zero dependency on `ZipArchive`.

---

## Security by default

- **httpOnly cookie, not localStorage.** The `access_token` cookie is `httpOnly`,
  `secure`, `SameSite=Lax`. The JWT payload never touches JavaScript.
- **Per-tenant isolation.** Every Eloquent model has a `scopeForUser(Builder $q, int $uid)`
  scope. Every controller calls `forUser($request->_user_id)` before any read or write.
  There is no code path that returns another user's data.
- **Zero hardcoded secrets.** `JWT_SECRET` and `DB_PASSWORD` are environment variables.
  `.env.example` is the only file with placeholders; it is never loaded in production.

---

## archiet-microcodegen-laravel vs the alternatives

| | `archiet-microcodegen-laravel` | `laravel new` | `laravel/breeze` |
|---|---|---|---|
| Input | Your PRD | Nothing | Nothing |
| Output | Full CRUD API for your entities | Empty skeleton | Auth scaffold only |
| Auth | JWT httpOnly cookie | Session / Sanctum | Session / Sanctum |
| Per-tenant isolation | Built-in (`scopeForUser`) | None | None |
| Entities | From your requirements | None | None |
| `docker-compose.yml` | ✅ | ❌ | ❌ |
| `openapi.yaml` | ✅ | ❌ | ❌ |
| `ARCHITECTURE.md` | ✅ ArchiMate 3.2 | ❌ | ❌ |
| LLM / API key | ❌ Never | ❌ | ❌ |

---

## FAQ

**Does the generated app really boot with `docker compose up`?**  
Yes. The generated `Dockerfile` runs a multi-stage PHP 8.3 build; `docker-compose.yml`
waits for Postgres `pg_isready` before starting Laravel. `php artisan migrate` runs as
part of the boot sequence.

**Is the generator itself pure PHP stdlib?**  
Yes. `archiet-microcodegen-laravel.php` uses only `gzdeflate`, `pack`, `preg_match`,
`json_encode`, `file_get_contents`, and `file_put_contents`. No Composer runtime
dependencies in the generator. The generated app has its own `composer.json`.

**What PHP version is required?**  
PHP ≥ 8.2 for the generator. The generated app targets PHP 8.3.

**What Laravel version does it generate?**  
Laravel 11, using the `bootstrap/app.php` bootstrap style (no `Http/Kernel.php`).

**What about Sanctum or Passport for auth?**  
The generated app uses a custom JWT middleware with no external package — one less
thing to configure. If you prefer Sanctum, the generator is a single PHP file; fork and
adapt Stage 3.

**What's NOT generated?**  
Queue workers, broadcasting, file uploads, mail templates, front-end scaffolding, and
multi-database support. For a full-stack app generated from your architecture diagram,
see [archiet.com](https://archiet.com?utm_source=packagist&utm_medium=package&utm_campaign=microcodegen-laravel).

---

## Why this exists

Architecture before code. A vibe-coded Laravel app has routes and models.
An *architected* Laravel app has a formal representation of why those routes and models
exist — what requirement they satisfy, what component they belong to, what boundaries
they must not cross.

`archiet-microcodegen-laravel` encodes that representation as an ArchiMate 3.2 genome
and renders it deterministically. Same PRD → same app. No hallucinations.

The genome is not a prompt. It is a typed intermediate representation: every entity has
an archimate type, every field has a domain type, every auth rule is a structural
constraint — not a comment in a template.

For teams that want a full architecture-to-code platform (multi-stack, governance, PRD
intake, quality scoring, delivery gates), visit
[archiet.com](https://archiet.com?utm_source=packagist&utm_medium=package&utm_campaign=microcodegen-laravel).

---

## Links

- **SDD guide:** [github.com/Anioko/spec-driven-development](https://github.com/Anioko/spec-driven-development)
- **Compliance guide:** [github.com/Anioko/compliance-from-architecture](https://github.com/Anioko/compliance-from-architecture) — SOC 2, GDPR, **EU AI Act Annex IV**
- **EU AI Act (deadline Aug 2026):** [Free risk classifier](https://archiet.com/tools/eu-ai-act-risk-classifier) · [Annex IV use case](https://archiet.com/use-cases/eu-ai-act-high-risk-ai-compliance)
- Full platform: [archiet.com](https://archiet.com?utm_source=packagist&utm_medium=package&utm_campaign=microcodegen-laravel)

*Generated with [archiet-microcodegen-laravel](https://packagist.org/packages/archiet/microcodegen-laravel)*
