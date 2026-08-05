# archiet-microcodegen-go

> **Generate a production-ready Go chi REST API from a requirements document. One command. No LLM. No API key. Pure Go stdlib in the generator. 922 lines you can read in 10 minutes.**

Inspired by Karpathy's `micrograd`: *this file is the complete algorithm. Everything else is just efficiency on top.*

```bash
go install github.com/aniekanasuquookono-web/archiet-microcodegen-go@latest
archiet-microcodegen-go -prd prd.md -out ./myapp/
cd myapp && docker compose up
# -> http://localhost:8080/api
```

Write a plain-English PRD. Get back a bootable Go chi app with GORM models, full CRUD handlers, JWT auth (httpOnly cookies), per-tenant data isolation, a Makefile, and a Postgres 16 docker-compose -- all without touching a template or hitting an AI API.

## Install

```bash
# Requires Go 1.21+
go install github.com/aniekanasuquookono-web/archiet-microcodegen-go@latest
```

The binary is placed in `$GOPATH/bin`. Ensure that directory is in your `$PATH`.

## Quick example

Save this as `prd.md`:

```markdown
# Task Manager

## Entities
- Task: title (string, required), description (text), status (string), due_date (date)
- Project: name (string, required), description (text)

## User Stories
As a user, I want to create tasks so I can track my work.
As a user, I want to assign tasks to projects so I can organise them.

## Integrations
- Stripe for billing
```

Run:

```bash
archiet-microcodegen-go -prd prd.md -out ./taskapp/
cd taskapp && docker compose up
```

You get a fully wired Go chi app: `Task` and `Project` GORM models, full CRUD handlers, JWT auth with httpOnly cookie middleware, per-tenant data isolation, a Makefile with `build` / `test` / `migrate` targets, `ARCHITECTURE.md` with ArchiMate 3.2 notation, and `openapi.yaml` -- zero modifications needed to boot.

## Use

**CLI**
```bash
# Write files to a directory:
archiet-microcodegen-go -prd prd.md -out ./myapp/
cd myapp && docker compose up

# Write ZIP:
archiet-microcodegen-go -prd prd.md -zip myapp.zip
```

**From source (no install)**
```bash
git clone https://github.com/aniekanasuquookono-web/archiet-microcodegen-go
go run . -prd prd.md -out ./myapp/
```

## What you get

| File | What it does |
|---|---|
| `main.go` | Go chi router bootstrap |
| `internal/database/db.go` | GORM + Postgres connection setup |
| `internal/auth/auth.go` | JWT sign/verify, bcrypt password hashing |
| `internal/auth/middleware.go` | JWT middleware reading httpOnly cookie |
| `internal/auth/handler.go` | POST /auth/register, POST /auth/login, POST /auth/logout |
| `internal/model/{entity}.go` | GORM model with `user_id` field (per-tenant) |
| `internal/handler/{entity}.go` | Full CRUD -- GET / POST / GET :id / PUT :id / DELETE :id |
| `go.mod` / `go.sum` | Go module with chi, GORM, golang-jwt deps |
| `Makefile` | `make build`, `make test`, `make migrate` |
| `docker-compose.yml` | Postgres 16 with healthcheck-gated startup |
| `Dockerfile` | Multi-stage Go 1.21 Alpine build |
| `ARCHITECTURE.md` | ArchiMate 3.2 element map |
| `openapi.yaml` | Machine-readable API contract |

**Every entity has per-tenant data isolation.** Every handler filters queries by `user_id` from the JWT. No cross-user data leaks.

## The four stages

```
PRD text
  |
  v ParsePRD()            -- regex extraction: entities, fields, user stories, integrations
Manifest struct
  |
  v ManifestToGenome()    -- maps to canonical IR with ArchiMate 3.2 element typing
Genome struct             (same schema as archiet.com full platform)
  |
  v RenderGenome()        -- Go chi + GORM + golang-jwt rendering
map[string]string         (path -> content)
  |
  v Pack()                -- archive/zip (pure Go stdlib)
ZIP file
```

The genome is the key: your PRD becomes an **ArchiMate 3.2 architecture document** before any code is generated -- traceable, maintainable, not just scaffolded.

## Why no LLMs

LLMs are great at understanding messy natural-language PRDs. They are unnecessary for the generation step -- once you have a clean manifest, code emission is deterministic. Zero hallucinations, zero non-determinism, same input always produces the same Go app.

The generator itself is pure Go stdlib (zero external imports in `main.go`). The generated app's `go.mod` lists `go-chi`, `gorm`, and `golang-jwt` -- deps of the app you are building, not the tool.

The full platform at [archiet.com](https://archiet.com?utm_source=pkg.go.dev&utm_medium=package&utm_campaign=microcodegen-go) handles LLM-powered extraction from complex PRDs, 14 target stacks, React/Next.js frontend, Expo mobile, and delivery gates.

## How it compares to manual scaffolding

| | Manual / `go mod init` | archiet-microcodegen-go |
|---|---|---|
| Starting point | Blank module, you write everything | PRD -> complete app |
| Auth | You implement | JWT httpOnly cookie middleware -- included |
| Data isolation | You implement | Per-user filtering on every handler -- built in |
| Database | You configure | `docker compose up` works immediately |
| Architecture docs | You write | `ARCHITECTURE.md` with ArchiMate 3.2 -- generated |
| API contract | You write | `openapi.yaml` -- generated |

## What's NOT here

- No LLM extraction (the full platform handles complex, messy PRDs)
- No React/Next.js frontend
- No Expo mobile app
- No Stripe wiring, rate limiting, audit logging
- No multi-stack (Go chi only here -- for NestJS, Java Spring Boot, Django, FastAPI see [archiet.com](https://archiet.com?utm_source=pkg.go.dev&utm_medium=package&utm_campaign=microcodegen-go))

## FAQ

**Does the generated app actually boot?**
Yes. `docker compose up` is the entire setup. GORM `AutoMigrate` creates the schema on first boot -- no manual migration needed.

**Is the generator itself pure stdlib?**
Yes. `main.go` imports only Go standard library packages (`archive/zip`, `crypto/rand`, `encoding/json`, `flag`, `os`, `path/filepath`, `regexp`, `strings`). The generated app's `go.mod` has external deps -- those are the app's deps, not the generator's.

**How is auth implemented?**
JWT is stored in an httpOnly cookie (`access_token`), never in a header body or localStorage. The middleware reads `r.Cookie("access_token")` and validates the HMAC-SHA256 signature. The JWT secret is generated per-app via `crypto/rand` and written to `.env.example`.

**How is per-tenant isolation enforced?**
Every GORM model has a `UserID` field. Every handler function filters by the `userID` extracted from the JWT before any DB query. There is no query path that returns another user's data.

**Why does `go install` require a separate GitHub repo?**
`go install module@version` resolves against the module path in `go.mod`. The module path must match the GitHub repository URL exactly. See `PUSH_TO_REPO.md` for deployment steps.

## Links

- **SDD guide:** [github.com/Anioko/spec-driven-development](https://github.com/Anioko/spec-driven-development)
- **Compliance guide:** [github.com/Anioko/compliance-from-architecture](https://github.com/Anioko/compliance-from-architecture) — SOC 2, GDPR, **EU AI Act Annex IV**
- **EU AI Act (deadline Aug 2026):** [Free risk classifier](https://archiet.com/tools/eu-ai-act-risk-classifier) · [Annex IV use case](https://archiet.com/use-cases/eu-ai-act-high-risk-ai-compliance)
- Source: [github.com/aniekanasuquookono-web/archiet-microcodegen-go](https://github.com/aniekanasuquookono-web/archiet-microcodegen-go)
- Full platform (14 stacks, frontend, mobile, deploy): [archiet.com](https://archiet.com?utm_source=pkg.go.dev&utm_medium=package&utm_campaign=microcodegen-go)
- Issues: [github.com/aniekanasuquookono-web/archiet-microcodegen-go/issues](https://github.com/aniekanasuquookono-web/archiet-microcodegen-go/issues)

## License

MIT.
