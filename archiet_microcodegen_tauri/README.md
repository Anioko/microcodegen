# archiet-microcodegen-tauri

> PRD text → working Tauri v2 desktop app → ZIP, in <1400 LOC, pure Rust stdlib, zero LLM calls.  
> Inspired by Karpathy's micrograd: this file is the complete algorithm.

[![Crates.io](https://img.shields.io/crates/v/archiet-microcodegen-tauri)](https://crates.io/crates/archiet-microcodegen-tauri)
[![Rust](https://img.shields.io/badge/rust-%3E%3D1.75-orange)](https://www.rust-lang.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## The fastest path from requirements to a running Tauri desktop app

You have a PRD — a Markdown file, a Confluence export, a Notion page.  
You want a **Tauri v2 desktop application** with real auth, a real local database,
and full CRUD for every entity in your spec — ready to `npm run tauri dev`.  
Most tools give you a prompt and a prayer. This gives you a ZIP in 3 seconds.

```bash
cargo install archiet-microcodegen-tauri
archiet-microcodegen-tauri prd.md --out ./my-app
cd my-app && npm install && npm run tauri dev
```

Your app boots on your desktop before the coffee is done.

## Install

```bash
# Global install via Cargo
cargo install archiet-microcodegen-tauri

# Or build from source
git clone https://github.com/aniekanasuquookono-web/archiet
cd archiet/archiet_microcodegen_tauri
cargo build --release
```

## Use

### CLI

```bash
# Write files to a directory
archiet-microcodegen-tauri prd.md --out ./my-app

# Write a ZIP instead
archiet-microcodegen-tauri prd.md --zip my-app.zip

# Print sample PRD to stdout
archiet-microcodegen-tauri --sample

# Then develop
cd my-app
npm install
npm run tauri dev
```

### Library

Add to your `Cargo.toml`:

```toml
[dependencies]
archiet-microcodegen-tauri = "0.1"
```

Call the four stages directly:

```rust
use archiet_microcodegen_tauri::{parse_prd, manifest_to_genome, render_genome, pack};

let text = std::fs::read_to_string("prd.md")?;
let manifest = parse_prd(&text);
let genome   = manifest_to_genome(manifest);
let files    = render_genome(&genome);

// Write to disk
write_disk(&files, std::path::Path::new("./output"));

// Or get a ZIP blob
let zip_bytes = pack(&files);
std::fs::write("output.zip", &zip_bytes)?;
```

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
  - due_date: string
  - priority: string
  - status: string
```

**Output:** a complete Tauri v2 app with `Project` and `Task` entities, per-user SQLite storage,
Argon2id auth, React/TypeScript frontend, typed IPC client, and `ARCHITECTURE.md` — ready to
`npm run tauri dev`.

## What you get

| File | What it does |
|---|---|
| `src-tauri/Cargo.toml` | Rust dependencies: tauri 2, rusqlite (bundled), argon2, uuid |
| `src-tauri/src/main.rs` | Tauri entry point |
| `src-tauri/src/lib.rs` | AppState (SQLite + session map), builder setup, IPC handler registration |
| `src-tauri/src/db.rs` | SQLite open + WAL migrations — one table per entity |
| `src-tauri/src/auth.rs` | `register_user`, `login_user`, `logout_user`, `get_me` IPC commands |
| `src-tauri/src/commands/{entity}_commands.rs` | `create_X`, `list_Xs`, `get_X`, `update_X`, `delete_X` — all per-user |
| `src-tauri/src/models/{entity}.rs` | Serde structs per entity |
| `src-tauri/tauri.conf.json` | Tauri v2 configuration |
| `src-tauri/capabilities/default.json` | Tauri v2 capability declarations |
| `src/ipc.ts` | Typed TypeScript client for every IPC command |
| `src/pages/Login.tsx` | Register + login form |
| `src/pages/{Entity}List.tsx` | CRUD list page per entity |
| `src/App.tsx` | React router with auth gate |
| `package.json` | Vite + React + @tauri-apps/api |
| `vite.config.ts` | Vite config tuned for Tauri |
| `tsconfig.json` | TypeScript strict mode |
| `ARCHITECTURE.md` | ArchiMate 3.2 ApplicationComponent + DataObject inventory |
| `openapi.yaml` | IPC command contract (not HTTP — Tauri invoke surface) |

## The four stages

```
parse_prd(text)              → Manifest   (entities, stories, integrations)
manifest_to_genome(manifest) → Genome     (ArchiMate 3.2 typed IR)
render_genome(genome)        → files      (Tauri v2 Rust + React/TypeScript source)
pack(files) / write_disk(files, dir)
```

**Stage 1** — regex-based PRD parser. Finds entities, fields (with types and required flags),
user stories, and third-party integrations without an LLM.

**Stage 2** — converts the manifest into a structured genome. Every entity automatically gains
`id`, `user_id`, and `created_at` fields. The genome drives all of Stage 3.

**Stage 3** — renders all Tauri Rust source files and React/TypeScript frontend. Key design decisions:
- SQLite (rusqlite, bundled) — no system SQLite required, ships in the binary
- Argon2id password hashing — bcrypt would also work but Argon2id is the current OWASP recommendation
- UUID session tokens stored in `AppState.sessions` (in-memory `HashMap`) — never on disk, never in localStorage
- Per-user isolation: every entity has a `user_id` FK; every query filters by it
- Tauri IPC commands (not HTTP) — typed with `#[tauri::command]`, invoked from TypeScript via `@tauri-apps/api`

**Stage 4** — writes files to disk or builds a valid ZIP (Store method, pure Rust stdlib).

## Why this exists

Architecture-first development before vibecoding. The genome is an ArchiMate 3.2
intermediate representation — your PRD becomes an architecture document, not just a prompt.

Archiet's full platform turns any PRD into a production-ready application across
nine stacks simultaneously, with quality scoring, delivery gates, and live preview.

**→ [archiet.com](https://archiet.com?utm_source=crates.io&utm_medium=package&utm_campaign=microcodegen-tauri)**

## Key differences from web microcodegen packages

| Concern | Web (Flask/NestJS/Go) | Tauri desktop |
|---|---|---|
| Auth storage | httpOnly cookies | In-memory session tokens (JS memory only) |
| Database | PostgreSQL | SQLite embedded (bundled, ships in binary) |
| API surface | HTTP REST | Tauri IPC commands (`invoke()`) |
| Deployment | Docker + cloud | `cargo build --release` → native binary |

## Links

- **SDD guide:** [github.com/Anioko/spec-driven-development](https://github.com/Anioko/spec-driven-development)
- **Compliance guide:** [github.com/Anioko/compliance-from-architecture](https://github.com/Anioko/compliance-from-architecture) — SOC 2, GDPR, **EU AI Act Annex IV**
- **EU AI Act (deadline Aug 2026):** [Free risk classifier](https://archiet.com/tools/eu-ai-act-risk-classifier) · [Annex IV use case](https://archiet.com/use-cases/eu-ai-act-high-risk-ai-compliance)

## License

MIT
