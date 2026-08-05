# archiet-microcodegen-nestjs

> **Generate a production-ready NestJS TypeScript REST API from a requirements document. One command. No LLM. No API key. Pure Node.js stdlib in the generator. 1115 lines you can read in 15 minutes.**

Inspired by Karpathy's `micrograd`: *this file is the complete algorithm. Everything else is just efficiency on top.*

```bash
npx archiet-microcodegen-nestjs prd.md --out ./myapp/
cd myapp && docker compose up
# -> http://localhost:3000
```

Write a plain-English PRD. Get back a bootable NestJS app with TypeORM entities, full CRUD controllers, Passport-JWT auth (httpOnly cookies -- never localStorage), per-tenant data isolation, and a Postgres 16 docker-compose -- all without touching a template or hitting an AI API.

## Install

```bash
# Zero-install: run once directly
npx archiet-microcodegen-nestjs prd.md --out ./myapp/

# Global install:
npm install -g archiet-microcodegen-nestjs
archiet-microcodegen-nestjs prd.md --out ./myapp/
```

Requires Node 18+ (ships with `npx`). Zero runtime npm dependencies in the generator.

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
npx archiet-microcodegen-nestjs prd.md --out ./taskapp/
cd taskapp && docker compose up
```

You get a fully wired NestJS app: `Task` and `Project` TypeORM entities with DTOs, full CRUD controllers, constructor-injected repository services, Passport-JWT reading an httpOnly cookie, per-tenant data isolation, `ARCHITECTURE.md` with ArchiMate 3.2 notation, and `openapi.yaml` -- zero modifications needed to boot.

## Use

**CLI**
```bash
# Write files to a directory (default):
npx archiet-microcodegen-nestjs prd.md --out ./myapp/
cd myapp && docker compose up

# Write a ZIP instead:
npx archiet-microcodegen-nestjs prd.md --zip myapp.zip
```

**Library**
```javascript
const { parsePrd, manifestToGenome, renderGenome, pack } = require('archiet-microcodegen-nestjs');

const manifest  = parsePrd(fs.readFileSync('prd.md', 'utf8'));
const genome    = manifestToGenome(manifest);
const files     = renderGenome(genome);
const zipBuffer = pack(files);   // Buffer
fs.writeFileSync('myapp.zip', zipBuffer);
```

## What you get

| File | What it does |
|---|---|
| `package.json` | NestJS + TypeORM + Passport-JWT + class-validator deps |
| `src/main.ts` | NestJS entry point with cookie-parser |
| `src/app.module.ts` | Root module with TypeORM PostgresQL config |
| `src/auth/auth.module.ts` | JWT + Passport + LocalStrategy wiring |
| `src/auth/auth.controller.ts` | register / login / logout / me (httpOnly cookie) |
| `src/auth/jwt.strategy.ts` | Reads httpOnly `access_token` cookie -- never Authorization header |
| `src/auth/jwt-auth.guard.ts` | `@UseGuards(JwtAuthGuard)` for protected routes |
| `src/{entity}/{Entity}.entity.ts` | TypeORM entity with `@Column`, `@PrimaryGeneratedColumn`, `userId` field |
| `src/{entity}/{entity}.dto.ts` | `Create{Entity}Dto` + `Update{Entity}Dto` with class-validator decorators |
| `src/{entity}/{entity}.service.ts` | Constructor-injected `@InjectRepository`, all queries filter by userId |
| `src/{entity}/{entity}.controller.ts` | Full CRUD: GET /xs, POST /xs, GET /xs/:id, PUT /xs/:id, DELETE /xs/:id |
| `src/{entity}/{entity}.spec.ts` | Jest happy-path unit tests |
| `docker-compose.yml` | Postgres 16 with healthcheck-gated startup |
| `ARCHITECTURE.md` | ArchiMate 3.2 element map |
| `openapi.yaml` | Machine-readable API contract |

**Every entity has per-tenant data isolation.** Every service method adds `.where("entity.userId = :userId", { userId })` to every TypeORM QueryBuilder call. No cross-user data leaks.

## The four stages

```
PRD text
  |
  v parsePrd(text)           -- regex extraction: entities, fields, user stories, integrations
manifest                     -- {solutionName, entities, userStories, integrations}
  |
  v manifestToGenome(manifest)   -- maps to canonical IR with ArchiMate 3.2 element typing
genome                           (same schema as archiet.com full platform)
  |
  v renderGenome(genome)     -- NestJS TypeScript code generation
{path: content}              -- every file the app needs
  |
  v pack(files)              -- custom PKZIP writer (pure Node zlib.deflateRawSync + CRC32)
ZIP Buffer
```

The genome is the key: your PRD becomes an **ArchiMate 3.2 architecture document** before any code is generated -- traceable, maintainable, not just scaffolded.

## Why no LLMs

LLMs are great at understanding messy natural-language PRDs. They are unnecessary for the generation step -- once you have a clean manifest, code emission is deterministic. Zero hallucinations, zero non-determinism, same input always produces the same NestJS app.

The generator uses pure Node.js stdlib (`fs`, `path`, `crypto`, `zlib`). Zero npm runtime dependencies. The generated app's `package.json` has NestJS, TypeORM, Passport -- deps of the app you are building, not the tool.

The full platform at [archiet.com](https://archiet.com?utm_source=npm&utm_medium=package&utm_campaign=microcodegen-nestjs) handles LLM-powered extraction from complex PRDs, 14 target stacks, React/Next.js frontend, Expo mobile, and delivery gates.

## How it compares to `nest new`

| | `nest new` | archiet-microcodegen-nestjs |
|---|---|---|
| Starting point | Empty module, no entities | PRD -> complete app |
| Entities | You create manually | Auto-generated from your requirements |
| Auth | You implement (or copy from docs) | Passport-JWT + httpOnly cookie -- included |
| Data isolation | You implement | `userId` filter on every query -- built in |
| Database | You configure | `docker compose up` works immediately |
| Architecture docs | You write | `ARCHITECTURE.md` with ArchiMate 3.2 -- generated |
| API contract | You write | `openapi.yaml` -- generated |
| DTOs + validation | You create | Create/Update DTOs with class-validator -- generated |

`nest new` gives you a starter. This gives you an app.

## What's NOT here

- No LLM extraction (the full platform handles complex, messy PRDs)
- No React/Next.js frontend
- No Expo mobile app
- No Stripe wiring, rate limiting, audit logging
- No multi-stack (NestJS only here -- for Go, Java Spring Boot, Django, FastAPI see [archiet.com](https://archiet.com?utm_source=npm&utm_medium=package&utm_campaign=microcodegen-nestjs))

## FAQ

**Does the generated app actually boot?**
Yes. `docker compose up` is the entire setup. TypeORM `synchronize: true` creates the schema on first boot -- no migration tooling needed.

**Is the generator itself zero-dependency?**
Yes. The entry script imports only Node.js built-ins (`fs`, `path`, `crypto`, `zlib`). Zero packages in `dependencies`. The generated app's `package.json` has NestJS, TypeORM, Passport -- those are the app's deps, not the generator's.

**How is auth implemented?**
JWT is stored in an httpOnly cookie, never in a response body or localStorage. `JwtStrategy` uses `ExtractJwt.fromExtractors([req => req?.cookies?.access_token])`. Cookie options: `{ httpOnly: true, sameSite: 'lax', maxAge: 7 * 86_400_000 }`.

**How is per-tenant isolation enforced?**
Every entity has a `userId: string` column. Every service method adds `.andWhere("entity.userId = :userId", { userId })` to every TypeORM QueryBuilder. There is no query path that can return another user's data.

**How do I add a real field to an entity?**
Edit your PRD to add the field, re-run the generator. Or edit the generated TypeORM entity, DTO, and service directly -- the code is yours.

**What happens to my PRD's integrations?**
The generator detects known integration keywords (Stripe, SendGrid, Twilio) and adds commented-out service stubs with setup instructions. The full platform at [archiet.com](https://archiet.com?utm_source=npm&utm_medium=package&utm_campaign=microcodegen-nestjs) wires them fully.

## Links

- **SDD guide:** [github.com/Anioko/spec-driven-development](https://github.com/Anioko/spec-driven-development)
- **Compliance guide:** [github.com/Anioko/compliance-from-architecture](https://github.com/Anioko/compliance-from-architecture) — SOC 2, GDPR, **EU AI Act Annex IV**
- **EU AI Act (deadline Aug 2026):** [Free risk classifier](https://archiet.com/tools/eu-ai-act-risk-classifier) · [Annex IV use case](https://archiet.com/use-cases/eu-ai-act-high-risk-ai-compliance)
- Source: [github.com/aniekanasuquookono-web/archiet](https://github.com/aniekanasuquookono-web/archiet/tree/feat/microcodegen-nestjs/archiet_microcodegen_nestjs)
- Full platform (14 stacks, frontend, mobile, deploy): [archiet.com](https://archiet.com?utm_source=npm&utm_medium=package&utm_campaign=microcodegen-nestjs)
- Issues: [github.com/aniekanasuquookono-web/archiet/issues](https://github.com/aniekanasuquookono-web/archiet/issues)

## License

MIT.
