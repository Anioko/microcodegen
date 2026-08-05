# archiet-microcodegen-java

> **Generate a production-ready Spring Boot 3 REST API from a requirements document. One command. No LLM. No API key. Pure Java stdlib in the generator. 421 lines you can read in 5 minutes.**

Inspired by Karpathy's `micrograd`: *this file is the complete algorithm. Everything else is just efficiency on top.*

```bash
java -jar archiet-microcodegen-java.jar prd.md --out ./myapp/
cd myapp && docker compose up
# -> http://localhost:8080/swagger-ui.html
```

Write a plain-English PRD. Get back a bootable Spring Boot 3 app with JPA entities, JpaRepository interfaces, @Transactional services, full CRUD controllers, Spring Security JWT auth (httpOnly cookies), per-tenant data isolation, and a Postgres 16 docker-compose -- all without touching a template or hitting an AI API.

## Install

Download the fat JAR (no Maven required):

```bash
curl -LO https://github.com/aniekanasuquookono-web/archiet-microcodegen-java/releases/latest/download/archiet-microcodegen-java.jar
java -jar archiet-microcodegen-java.jar --help
```

Or build from source:

```bash
git clone https://github.com/aniekanasuquookono-web/archiet-microcodegen-java
cd archiet-microcodegen-java
mvn package -q
java -jar target/archiet-microcodegen-java-0.1.0.jar --help
```

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
java -jar archiet-microcodegen-java.jar prd.md --out ./taskapp/
cd taskapp && docker compose up
```

You get a fully wired Spring Boot 3 app: `Task` and `Project` JPA entities, JpaRepository interfaces, @Transactional service layer, full CRUD @RestController, Spring Security JWT filter reading httpOnly cookie, per-tenant data isolation, `ARCHITECTURE.md` with ArchiMate 3.2 notation, and `openapi.yaml` -- zero modifications needed to boot.

## Use

**CLI**
```bash
# Write files to a directory:
java -jar archiet-microcodegen-java.jar prd.md --out ./myapp/
cd myapp && docker compose up

# Write ZIP:
java -jar archiet-microcodegen-java.jar prd.md --zip myapp.zip
```

## What you get

| File | What it does |
|---|---|
| `pom.xml` | Spring Boot 3 + JPA + Spring Security + Lombok deps |
| `src/main/java/.../Application.java` | Spring Boot entry point |
| `src/main/java/.../model/User.java` | JPA User entity with bcrypt password |
| `src/main/java/.../controller/AuthController.java` | register / login / logout / me (httpOnly cookie JWT) |
| `src/main/java/.../security/JwtFilter.java` | Spring Security filter reading httpOnly cookie |
| `src/main/java/.../security/SecurityConfig.java` | Stateless Spring Security config |
| `src/main/java/.../model/{Entity}.java` | JPA entity with Lombok @Data, userId field (per-tenant) |
| `src/main/java/.../repository/{Entity}Repository.java` | JpaRepository with findAllByUserId, findByIdAndUserId |
| `src/main/java/.../service/{Entity}Service.java` | @Transactional service layer |
| `src/main/java/.../controller/{Entity}Controller.java` | Full CRUD (@RestController) |
| `src/main/resources/application.properties` | PostgreSQL datasource + JWT secret from env |
| `docker-compose.yml` | Postgres 16 with healthcheck-gated startup |
| `Dockerfile` | Multi-stage Java 17 Alpine build |
| `ARCHITECTURE.md` | ArchiMate 3.2 element map |
| `openapi.yaml` | Machine-readable API contract |

**Every entity has per-tenant data isolation.** Every JpaRepository method filters by `userId`. No cross-user data leaks.

## The four stages

```
PRD text
  |
  v Stage1.parsePrd(text)       -- regex extraction: entities, fields, user stories, integrations
Manifest record
  |
  v Stage2.toGenome(manifest)   -- maps to canonical IR with ArchiMate 3.2 element typing
Genome record                   (same schema as archiet.com full platform)
  |
  v Stage3.renderGenome(genome) -- Spring Boot 3 + JPA + Spring Security rendering
Map<String, String>             (path -> content)
  |
  v Stage4.pack(files, outPath) -- java.util.zip.ZipOutputStream (pure stdlib)
ZIP file
```

The genome is the key: your PRD becomes an **ArchiMate 3.2 architecture document** before any code is generated -- traceable, maintainable, not just scaffolded.

## Why no LLMs

LLMs are great at understanding messy natural-language PRDs. They are unnecessary for the generation step -- once you have a clean manifest, code emission is deterministic. Zero hallucinations, zero non-determinism, same input always produces the same Spring Boot app.

The generator JAR uses pure `java.*` stdlib -- no JJWT, no Jackson, no Spring in the generator itself. The generated app's `pom.xml` lists Spring Boot, JPA, Spring Security -- deps of the app you are building, not the tool.

The full platform at [archiet.com](https://archiet.com?utm_source=maven&utm_medium=package&utm_campaign=microcodegen-java) handles LLM-powered extraction from complex PRDs, 14 target stacks, React/Next.js frontend, Expo mobile, and delivery gates.

## How it compares to Spring Initializr

| | Spring Initializr | archiet-microcodegen-java |
|---|---|---|
| Starting point | Blank app with stubs, you write everything | PRD -> complete app |
| Entities | You create manually | Auto-generated from your requirements |
| Auth | You implement | Spring Security + JWT httpOnly cookie -- included |
| Data isolation | You implement | Per-user JpaRepository methods -- built in |
| Database | You configure | `docker compose up` works immediately |
| Architecture docs | You write | `ARCHITECTURE.md` with ArchiMate 3.2 -- generated |
| API contract | You write | `openapi.yaml` -- generated |

Spring Initializr gives you a starter. This gives you an app.

## What's NOT here

- No LLM extraction (the full platform handles complex, messy PRDs)
- No React/Next.js frontend
- No Expo mobile app
- No Stripe wiring, rate limiting, audit logging
- No multi-stack (Java Spring Boot only here -- for Go, NestJS, Django, FastAPI see [archiet.com](https://archiet.com?utm_source=maven&utm_medium=package&utm_campaign=microcodegen-java))

## FAQ

**Does the generated app actually boot?**
Yes. `docker compose up` is the entire setup. Spring Boot auto-creates the schema via JPA `ddl-auto: update` on first boot -- no Flyway or Liquibase setup needed.

**Is the generator itself pure stdlib?**
Yes. `Main.java` imports only `java.*` packages. JWT signing uses `javax.crypto.Mac` with HMAC-SHA256. The generated app's `pom.xml` has Spring Boot, JPA, Spring Security -- those are the app's deps, not the generator's.

**How is auth implemented?**
JWT is stored in an httpOnly cookie, never in a response body or localStorage. `JwtFilter` reads the `access_token` cookie, Base64URL-decodes the payload, and validates the HMAC-SHA256 signature. Cookie options include `httpOnly=true`, `sameSite=Lax`, and a 7-day expiry.

**How is per-tenant isolation enforced?**
Every JPA entity has a `userId` field. JpaRepository interfaces declare `findAllByUserId(String userId)` and `findByIdAndUserId(String id, String userId)` -- Spring Data generates the queries from these method names. Every service method passes the authenticated userId. There is no query path that returns another user's data.

**What Java version is required?**
Java 17+ for both running the generator and building the generated app (uses records, text blocks). The generated Dockerfile targets Java 17 Alpine.

**Does it work with Maven?**
Yes -- the generator is a standard Maven project. Build with `mvn package`. The generated app is also a standard Maven project -- open in IntelliJ, Eclipse, or VS Code with the Java extension pack.

## Links

- **SDD guide:** [github.com/Anioko/spec-driven-development](https://github.com/Anioko/spec-driven-development)
- **Compliance guide:** [github.com/Anioko/compliance-from-architecture](https://github.com/Anioko/compliance-from-architecture) — SOC 2, GDPR, **EU AI Act Annex IV**
- **EU AI Act (deadline Aug 2026):** [Free risk classifier](https://archiet.com/tools/eu-ai-act-risk-classifier) · [Annex IV use case](https://archiet.com/use-cases/eu-ai-act-high-risk-ai-compliance)
- Source: [github.com/aniekanasuquookono-web/archiet-microcodegen-java](https://github.com/aniekanasuquookono-web/archiet-microcodegen-java)
- Full platform (14 stacks, frontend, mobile, deploy): [archiet.com](https://archiet.com?utm_source=maven&utm_medium=package&utm_campaign=microcodegen-java)
- Issues: [github.com/aniekanasuquookono-web/archiet-microcodegen-java/issues](https://github.com/aniekanasuquookono-web/archiet-microcodegen-java/issues)

## License

MIT.
