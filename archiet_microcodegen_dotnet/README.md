# archiet-microcodegen-dotnet

> PRD text → working ASP.NET Core 8 app → ZIP, in <1400 LOC, pure .NET BCL, zero LLM calls.  
> Inspired by Karpathy's micrograd: this file is the complete algorithm.

[![NuGet](https://img.shields.io/nuget/v/archiet-microcodegen-dotnet)](https://www.nuget.org/packages/archiet-microcodegen-dotnet)
[![.NET](https://img.shields.io/badge/.NET-8.0-512BD4)](https://dotnet.microsoft.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## The fastest path from requirements to a running ASP.NET Core REST API

You have a PRD (a Markdown file, a Confluence export, a Notion page).  
You want an **ASP.NET Core 8 Web API** with real auth, a real database, and real routing — ready to `docker compose up`.  
Most generators give you a skeleton. This gives you *your* app in 3 seconds.

```bash
dotnet tool install -g archiet-microcodegen-dotnet
archiet-microcodegen-dotnet prd.md --out ./my-app
cd my-app && cp .env.example .env && docker compose up
```

First request hits `/api/auth/register` before the coffee is done.

---

## Install

```bash
# Global dotnet tool install (recommended)
dotnet tool install -g archiet-microcodegen-dotnet

# Or build from source
git clone https://github.com/aniekanasuquookono-web/archiet
cd archiet/archiet_microcodegen_dotnet
dotnet run -- prd.md --out ./my-app
```

---

## Use

### CLI

```bash
# Write files to a directory
archiet-microcodegen-dotnet prd.md --out ./my-api

# Write a ZIP instead
archiet-microcodegen-dotnet prd.md --zip my-api.zip

# Then boot
cd my-api
cp .env.example .env         # edit DATABASE_URL, JWT_SECRET
docker compose up            # Postgres + ASP.NET Core
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

**Output:** a complete ASP.NET Core 8 app with `Project` and `Task` EF Core models,
per-tenant isolation, JWT auth in an httpOnly cookie, migrations, Dockerfile, and
`openapi.yaml` — ready to `docker compose up`.

---

## What you get

| File | What it does |
|---|---|
| `{AppName}.csproj` | SDK-style project, `net8.0`, EF Core + Npgsql + BCrypt NuGet refs |
| `Program.cs` | Minimal hosting model: DI wiring, middleware, `Database.Migrate()` on boot |
| `Data/AppDbContext.cs` | `DbContext` with a `DbSet` per entity |
| `Models/User.cs` | `Id`, `Name`, `Email`, `PasswordHash`, `CreatedAt` |
| `Models/{Entity}.cs` | `Id`, `UserId` (FK), domain fields, `CreatedAt`, `UpdatedAt` |
| `DTOs/{Entity}Dto.cs` | Input DTO for create / update |
| `Services/JwtService.cs` | `Encode(userId, email)` / `Decode(token)` — pure BCL, no external package |
| `Auth/JwtMiddleware.cs` | Validates `access_token` cookie, injects `UserId` into `HttpContext.Items` |
| `Controllers/AuthController.cs` | `register`, `login`, `logout`, `me` |
| `Controllers/{Entity}Controller.cs` | Full CRUD, every query `.Where(e => e.UserId == Uid)` |
| `appsettings.json` | Minimal logging config |
| `.env.example` | All required env vars pre-documented |
| `Dockerfile` | Multi-stage `mcr.microsoft.com/dotnet/sdk:8.0-alpine` build |
| `docker-compose.yml` | App + Postgres 16, healthcheck-gated |
| `ARCHITECTURE.md` | ArchiMate 3.2 ApplicationComponent + DataObject inventory |
| `openapi.yaml` | Machine-readable API contract |

---

## The four stages

```
ParsePrd(text)              → Manifest   (entities, stories, integrations)
ManifestToGenome(manifest)  → Genome     (ArchiMate 3.2 typed IR)
RenderGenome(genome)        → files      (ASP.NET Core 8 C# source)
PackZip(files) / WriteDisk(files, dir)
```

**Stage 1** — regex-based PRD parser. Finds entities, fields, user stories, and
third-party integrations (Stripe, SendGrid, Twilio, …) without an LLM.

**Stage 2** — converts the manifest into a typed `Genome` record. Every entity gains
`Id`, `UserId`, `CreatedAt`, `UpdatedAt` automatically. The genome is a plain C# record
— no reflection, no attributes, no magic.

**Stage 3** — renders all ASP.NET Core files from the genome. `JwtService` is a pure BCL
implementation using `HMACSHA256` and `CryptographicOperations.FixedTimeEquals` — no
`Microsoft.IdentityModel.Tokens` package required in the generator.

**Stage 4** — writes files using `System.IO.Compression.ZipArchive` (BCL). No NuGet
dependency for the generator. The generated app references EF Core, Npgsql, and BCrypt
in its own `.csproj`.

---

## Security by default

- **httpOnly cookie, not localStorage.** The `access_token` cookie is `HttpOnly = true`,
  `SameSite = Lax`, `Expires = +7 days`. The JWT payload never touches JavaScript.
- **Per-tenant isolation.** Every EF Core query includes `.Where(e => e.UserId == Uid)`.
  `Uid` comes from `HttpContext.Items["UserId"]` set by the JWT middleware. There is no
  code path that returns another user's data.
- **Constant-time signature comparison.** `CryptographicOperations.FixedTimeEquals`
  prevents timing attacks on the JWT signature check.
- **Zero hardcoded secrets.** `JWT_SECRET` and `DATABASE_URL` are environment variables.
  The app throws `InvalidOperationException` at startup if either is missing.

---

## archiet-microcodegen-dotnet vs the alternatives

| | `archiet-microcodegen-dotnet` | `dotnet new webapi` | Visual Studio scaffolding |
|---|---|---|---|
| Input | Your PRD | Nothing | Single model name |
| Output | Full CRUD API for all entities | Empty skeleton | One CRUD controller |
| Auth | JWT httpOnly cookie + BCrypt | None | Identity (session-based) |
| Per-tenant isolation | Built-in (`.Where(e => e.UserId == Uid)`) | None | None |
| `docker-compose.yml` | ✅ | ❌ | ❌ |
| `openapi.yaml` | ✅ | Swagger UI only | ❌ |
| `ARCHITECTURE.md` | ✅ ArchiMate 3.2 | ❌ | ❌ |
| LLM / API key | ❌ Never | ❌ | ❌ |

---

## FAQ

**Does the generated app really boot with `docker compose up`?**  
Yes. The generated `Dockerfile` uses a multi-stage `dotnet/sdk:8.0-alpine` build.
`docker-compose.yml` waits for Postgres `pg_isready` before starting the app.
`Database.Migrate()` runs at app startup — no manual migration step.

**Is the generator itself pure .NET BCL?**  
Yes. `Program.cs` uses only `System.IO.Compression`, `System.Text.RegularExpressions`,
`System.Security.Cryptography`, and `System.Linq`. No NuGet packages in the generator's
own `.csproj`. The generated app has its own NuGet dependencies.

**Why BCrypt for password hashing instead of `PasswordHasher<T>`?**  
The generated app uses `BCrypt.Net-Next` because it is the most widely understood
.NET password hashing library. If you prefer `PasswordHasher<T>` (ASP.NET Core Identity),
the generator is a single C# file — adapt Stage 3.

**What EF Core provider does it use?**  
Npgsql (PostgreSQL). The generated `DATABASE_URL` format is
`Host=db;Database=app;Username=app;Password=changeme` — standard Npgsql connection string.

**Does it generate EF Core migrations?**  
The generated app calls `Database.Migrate()` on boot, which applies pending migrations.
The migrations themselves are not pre-generated in the ZIP — run `dotnet ef migrations add Init`
once after setting up your database. Alternatively, replace `Database.Migrate()` with
`Database.EnsureCreated()` for development convenience.

**What's NOT generated?**  
Background workers, SignalR hubs, gRPC, Blazor/Razor views, Identity UI, multi-schema
multi-tenancy, and health check endpoints. For a full-stack app generated from your
architecture diagram, see
[archiet.com](https://archiet.com?utm_source=nuget&utm_medium=package&utm_campaign=microcodegen-dotnet).

---

## Why this exists

Architecture before code. A vibe-coded .NET app has controllers and DbSets.
An *architected* .NET app has a formal representation of why those controllers and DbSets
exist — what requirement they satisfy, what component they belong to, what boundaries
they must not cross.

`archiet-microcodegen-dotnet` encodes that representation as an ArchiMate 3.2 genome
and renders it deterministically. Same PRD → same app. No hallucinations.

The genome is not a prompt. It is a typed intermediate representation: every entity maps
to a C# `record`, every field has a CLR type, every auth rule is a structural constraint
— not a comment in a template.

For teams that want a full architecture-to-code platform (multi-stack, governance, PRD
intake, quality scoring, delivery gates), visit
[archiet.com](https://archiet.com?utm_source=nuget&utm_medium=package&utm_campaign=microcodegen-dotnet).

---

## Links

- **SDD guide:** [github.com/Anioko/spec-driven-development](https://github.com/Anioko/spec-driven-development)
- **Compliance guide:** [github.com/Anioko/compliance-from-architecture](https://github.com/Anioko/compliance-from-architecture) — SOC 2, GDPR, **EU AI Act Annex IV**
- **EU AI Act (deadline Aug 2026):** [Free risk classifier](https://archiet.com/tools/eu-ai-act-risk-classifier) · [Annex IV use case](https://archiet.com/use-cases/eu-ai-act-high-risk-ai-compliance)
- Full platform: [archiet.com](https://archiet.com?utm_source=nuget&utm_medium=package&utm_campaign=microcodegen-dotnet)

*Generated with [archiet-microcodegen-dotnet](https://www.nuget.org/packages/archiet-microcodegen-dotnet)*
