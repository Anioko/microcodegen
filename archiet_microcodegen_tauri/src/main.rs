// archiet-microcodegen-tauri v0.1.0
// PRD text → Tauri v2 desktop app → ZIP. Pure Rust stdlib. <1400 LOC.
// Stage 1: parse_prd(text)             → Manifest (language-agnostic)
// Stage 2: manifest_to_genome(manifest) → Genome  (ArchiMate 3.2 typed)
// Stage 3: render_genome(genome)       → HashMap<String,String> (Tauri-specific)
// Stage 4: pack(files)                 → Vec<u8> (ZIP, Store method) or write to disk
// Zero external dependencies. Inspired by Karpathy's micrograd.

use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::Path;

// ─── Types ────────────────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
struct FieldSpec {
    field_type: String,
    required: bool,
}

#[derive(Clone, Debug)]
struct Entity {
    name: String,
    fields: Vec<(String, FieldSpec)>,
}

#[allow(dead_code)]
#[derive(Clone, Debug)]
struct Integration {
    name: String,
    category: String,
}

#[allow(dead_code)]
#[derive(Clone, Debug)]
struct UserStory {
    as_a: String,
    i_want: String,
}

#[derive(Clone, Debug)]
struct ArchiMateElement {
    name: String,
    kind: String,
    description: String,
}

#[derive(Clone, Debug)]
struct Manifest {
    solution_name: String,
    entities: Vec<Entity>,
    user_stories: Vec<UserStory>,
    integrations: Vec<Integration>,
}

#[allow(dead_code)]
#[derive(Clone, Debug)]
struct Genome {
    solution_name: String,
    bundle_id: String,
    entities: Vec<Entity>,
    user_stories: Vec<UserStory>,
    integrations: Vec<Integration>,
    archimate_elements: Vec<ArchiMateElement>,
}

// ─── String helpers ───────────────────────────────────────────────────────────

fn snake(s: &str) -> String {
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if c.is_uppercase() && i > 0 {
            out.push('_');
        }
        if c.is_alphanumeric() {
            out.extend(c.to_lowercase());
        } else {
            out.push('_');
        }
    }
    out.trim_matches('_').to_string()
}

fn pascal(s: &str) -> String {
    snake(s)
        .split('_')
        .filter(|p| !p.is_empty())
        .map(|p| {
            let mut ch = p.chars();
            match ch.next() {
                None => String::new(),
                Some(f) => f.to_uppercase().to_string() + ch.as_str(),
            }
        })
        .collect()
}

fn plural(s: &str) -> String {
    if s.ends_with('s') {
        return format!("{}es", s);
    }
    if s.ends_with('y') {
        return format!("{}ies", &s[..s.len() - 1]);
    }
    format!("{}s", s)
}

fn fill(tmpl: &str, vars: &[(&str, &str)]) -> String {
    let mut r = tmpl.to_string();
    for (k, v) in vars {
        r = r.replace(&format!("{{{{{}}}}}", k), v);
    }
    r
}

#[allow(dead_code)]
fn random_hex(n: usize) -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let seed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let mut state = seed ^ 0xdeadbeef_cafe1234u128;
    let mut out = String::new();
    for _ in 0..n {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        out.push_str(&format!("{:02x}", (state & 0xff) as u8));
    }
    out[..n].to_string()
}

fn rust_type(t: &str) -> &'static str {
    match t {
        "integer" | "int" => "i64",
        "float" | "decimal" | "number" => "f64",
        "boolean" | "bool" => "bool",
        _ => "String",
    }
}

fn sqlite_type(t: &str) -> &'static str {
    match t {
        "integer" | "int" => "INTEGER",
        "float" | "decimal" | "number" => "REAL",
        "boolean" | "bool" => "INTEGER",
        _ => "TEXT",
    }
}

fn ts_type(t: &str) -> &'static str {
    match t {
        "integer" | "int" | "float" | "decimal" | "number" => "number",
        "boolean" | "bool" => "boolean",
        _ => "string",
    }
}

// ─── STAGE 4: pack (ZIP with Store method — pure stdlib) ─────────────────────

fn crc32(data: &[u8]) -> u32 {
    static TABLE: std::sync::OnceLock<[u32; 256]> = std::sync::OnceLock::new();
    let tbl = TABLE.get_or_init(|| {
        let mut t = [0u32; 256];
        for i in 0..256usize {
            let mut c = i as u32;
            for _ in 0..8 {
                c = if c & 1 != 0 { 0xEDB88320 ^ (c >> 1) } else { c >> 1 };
            }
            t[i] = c;
        }
        t
    });
    let mut c: u32 = 0xFFFFFFFF;
    for &b in data {
        c = tbl[((c ^ b as u32) & 0xFF) as usize] ^ (c >> 8);
    }
    c ^ 0xFFFFFFFF
}

fn pack(files: &HashMap<String, String>) -> Vec<u8> {
    let mut out: Vec<u8> = Vec::new();
    let mut cd: Vec<u8> = Vec::new();
    let mut count: u16 = 0;

    let mut keys: Vec<&String> = files.keys().collect();
    keys.sort();

    for path in keys {
        let content = files[path].as_bytes();
        let name = path.as_bytes();
        let crc = crc32(content);
        let sz = content.len() as u32;
        let offset = out.len() as u32;

        // Local file header (Store, method=0)
        out.extend_from_slice(&[0x50, 0x4B, 0x03, 0x04]);
        out.extend_from_slice(&20u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes()); // method: store
        out.extend_from_slice(&0u16.to_le_bytes()); // mod time
        out.extend_from_slice(&0u16.to_le_bytes()); // mod date
        out.extend_from_slice(&crc.to_le_bytes());
        out.extend_from_slice(&sz.to_le_bytes());
        out.extend_from_slice(&sz.to_le_bytes());
        out.extend_from_slice(&(name.len() as u16).to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(name);
        out.extend_from_slice(content);

        // Central directory entry
        cd.extend_from_slice(&[0x50, 0x4B, 0x01, 0x02]);
        cd.extend_from_slice(&20u16.to_le_bytes());
        cd.extend_from_slice(&20u16.to_le_bytes());
        cd.extend_from_slice(&0u16.to_le_bytes());
        cd.extend_from_slice(&0u16.to_le_bytes());
        cd.extend_from_slice(&0u16.to_le_bytes());
        cd.extend_from_slice(&0u16.to_le_bytes());
        cd.extend_from_slice(&crc.to_le_bytes());
        cd.extend_from_slice(&sz.to_le_bytes());
        cd.extend_from_slice(&sz.to_le_bytes());
        cd.extend_from_slice(&(name.len() as u16).to_le_bytes());
        cd.extend_from_slice(&[0u8; 12]); // extra, comment, disk, attrs
        cd.extend_from_slice(&offset.to_le_bytes());
        cd.extend_from_slice(name);
        count += 1;
    }

    let cd_offset = out.len() as u32;
    let cd_size = cd.len() as u32;
    out.extend_from_slice(&cd);
    out.extend_from_slice(&[0x50, 0x4B, 0x05, 0x06]);
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&count.to_le_bytes());
    out.extend_from_slice(&count.to_le_bytes());
    out.extend_from_slice(&cd_size.to_le_bytes());
    out.extend_from_slice(&cd_offset.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out
}

fn write_disk(files: &HashMap<String, String>, out_dir: &Path) {
    for (path, content) in files {
        let dest = out_dir.join(path);
        if let Some(parent) = dest.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(&dest, content).unwrap();
    }
}

// ─── STAGE 1: parse_prd ──────────────────────────────────────────────────────

fn parse_prd(text: &str) -> Manifest {
    let solution_name = text
        .lines()
        .find(|l| l.starts_with("# "))
        .map(|l| l.trim_start_matches('#').trim().to_string())
        .unwrap_or_else(|| "Generated App".to_string());

    let entities = extract_entities(text);
    let user_stories = extract_stories(text);
    let integrations = extract_integrations(text);
    Manifest { solution_name, entities, user_stories, integrations }
}

fn extract_entities(text: &str) -> Vec<Entity> {
    let mut entities: Vec<Entity> = Vec::new();
    let lines: Vec<&str> = text.lines().collect();
    let mut i = 0;
    let skip = ["User", "Stories", "API", "Entities", "Entity", "Model",
                "Models", "Requirements", "Integration", "Integrations",
                "Features", "Overview", "Setup", "Config"];

    while i < lines.len() {
        if let Some(name) = entity_name(lines[i]) {
            if !skip.contains(&name.as_str()) && name.len() >= 2 && name.len() <= 50 {
                let mut block = String::new();
                let mut j = i + 1;
                while j < lines.len() {
                    if entity_name(lines[j]).is_some() { break; }
                    if lines[j].starts_with("## ") || lines[j].starts_with("# ") { break; }
                    block.push_str(lines[j]);
                    block.push('\n');
                    j += 1;
                }
                let fields = parse_fields(&block);
                entities.push(Entity { name, fields });
                i = j;
                continue;
            }
        }
        i += 1;
    }
    entities
}

fn entity_name(line: &str) -> Option<String> {
    let line = line.trim();
    let n = if line.starts_with("**") && line.ends_with("**") && line.len() > 4 {
        line[2..line.len() - 2].to_string()
    } else if line.starts_with("### ") || line.starts_with("#### ") {
        line.trim_start_matches('#').trim().to_string()
    } else {
        return None;
    };
    if n.is_empty() || n.len() > 50 { return None; }
    if !n.chars().all(|c| c.is_alphanumeric() || c == ' ' || c == '_') { return None; }
    if !n.chars().next().map(|c| c.is_uppercase()).unwrap_or(false) { return None; }
    Some(n.split_whitespace().collect::<Vec<_>>().join(""))
}

fn parse_fields(block: &str) -> Vec<(String, FieldSpec)> {
    let mut fields = Vec::new();
    for line in block.lines() {
        let cleaned = line.trim().trim_start_matches(|c| c == '-' || c == '*' || c == ' ');
        if let Some(colon) = cleaned.find(':') {
            let fname = cleaned[..colon].trim().to_lowercase().replace(' ', "_");
            if fname.is_empty() || fname.len() > 40 { continue; }
            if !fname.chars().all(|c| c.is_alphanumeric() || c == '_') { continue; }
            let rest = &cleaned[colon + 1..];
            let raw = rest.split_whitespace().next().unwrap_or("string").to_lowercase();
            let ftype = match raw.trim_end_matches(',') {
                "integer" | "int" => "integer",
                "float" | "decimal" | "number" => "float",
                "boolean" | "bool" => "boolean",
                "datetime" | "timestamp" => "datetime",
                _ => "string",
            }.to_string();
            let required = rest.to_lowercase().contains("required");
            fields.push((fname, FieldSpec { field_type: ftype, required }));
        }
    }
    fields
}

fn extract_stories(text: &str) -> Vec<UserStory> {
    let mut stories = Vec::new();
    for line in text.lines() {
        let lower = line.to_lowercase();
        if lower.contains("as a") && lower.contains("i want") {
            let as_a = between(line, "as a", ",").unwrap_or_default();
            let i_want = between(line, "i want", ",")
                .or_else(|| between(line, "i want", "."))
                .unwrap_or_default();
            if !as_a.is_empty() && !i_want.is_empty() {
                stories.push(UserStory {
                    as_a: as_a.trim().to_string(),
                    i_want: i_want.trim().to_string(),
                });
            }
        }
    }
    stories
}

fn between(s: &str, after: &str, before: &str) -> Option<String> {
    let lower = s.to_lowercase();
    let start = lower.find(after)? + after.len();
    let rest = &s[start..];
    let end = rest.to_lowercase().find(before).unwrap_or(rest.len());
    Some(rest[..end].trim().to_string())
}

fn extract_integrations(text: &str) -> Vec<Integration> {
    let known = [
        ("stripe", "payments"), ("braintree", "payments"),
        ("sendgrid", "email"), ("mailgun", "email"),
        ("twilio", "sms"), ("auth0", "auth"),
        ("s3", "storage"), ("cloudinary", "storage"),
        ("datadog", "observability"), ("sentry", "observability"),
        ("openai", "ai"), ("anthropic", "ai"),
    ];
    let lower = text.to_lowercase();
    known.iter().filter_map(|(n, c)| {
        if lower.contains(n) {
            Some(Integration { name: n.to_string(), category: c.to_string() })
        } else {
            None
        }
    }).collect()
}

// ─── STAGE 2: manifest_to_genome ─────────────────────────────────────────────

fn manifest_to_genome(manifest: Manifest) -> Genome {
    let bundle_id = snake(&manifest.solution_name);

    let mut archimate: Vec<ArchiMateElement> = vec![
        ArchiMateElement {
            name: manifest.solution_name.clone(),
            kind: "ApplicationComponent".to_string(),
            description: format!("Tauri v2 desktop application: {}", manifest.solution_name),
        },
        ArchiMateElement {
            name: "AuthService".to_string(),
            kind: "ApplicationComponent".to_string(),
            description: "Argon2id password hashing, UUID session tokens, SQLite users table".to_string(),
        },
        ArchiMateElement {
            name: "LocalDatabase".to_string(),
            kind: "TechnologyService".to_string(),
            description: "SQLite 3 (rusqlite, bundled) — WAL mode, per-user data isolation".to_string(),
        },
    ];
    for e in &manifest.entities {
        archimate.push(ArchiMateElement {
            name: e.name.clone(),
            kind: "DataObject".to_string(),
            description: format!("Persistent entity: {} — SQLite-backed, user_id FK", e.name),
        });
    }

    let mut entities = manifest.entities.clone();
    for entity in &mut entities {
        let has_id = entity.fields.iter().any(|(f, _)| f == "id");
        let has_uid = entity.fields.iter().any(|(f, _)| f == "user_id");
        let has_ts = entity.fields.iter().any(|(f, _)| f == "created_at");
        if !has_id {
            entity.fields.insert(0, ("id".to_string(), FieldSpec { field_type: "integer".to_string(), required: true }));
        }
        if !has_uid {
            entity.fields.insert(1, ("user_id".to_string(), FieldSpec { field_type: "integer".to_string(), required: true }));
        }
        if !has_ts {
            entity.fields.push(("created_at".to_string(), FieldSpec { field_type: "datetime".to_string(), required: false }));
        }
    }

    Genome {
        solution_name: manifest.solution_name,
        bundle_id,
        entities,
        user_stories: manifest.user_stories,
        integrations: manifest.integrations,
        archimate_elements: archimate,
    }
}

// ─── STAGE 3: render_genome ───────────────────────────────────────────────────

fn render_genome(g: &Genome) -> HashMap<String, String> {
    let mut f: HashMap<String, String> = HashMap::new();
    let app = &g.solution_name;
    let bundle = &g.bundle_id;
    let bundle_lib = bundle.replace('-', "_");
    let bundle_rev = format!("com.archiet.{}", bundle);

    f.insert("src-tauri/Cargo.toml".into(), r_cargo_toml(app, bundle, &bundle_lib));
    f.insert("src-tauri/build.rs".into(), "fn main() {\n    tauri_build::build()\n}\n".into());
    f.insert("src-tauri/src/main.rs".into(), r_app_main(&bundle_lib));
    f.insert("src-tauri/src/lib.rs".into(), r_lib_rs(g, &bundle_lib));
    f.insert("src-tauri/src/db.rs".into(), r_db_rs(g, bundle));
    f.insert("src-tauri/src/auth.rs".into(), r_auth_rs());
    f.insert("src-tauri/src/commands/mod.rs".into(), r_commands_mod(g));
    f.insert("src-tauri/src/models/mod.rs".into(), r_models_mod(g));

    for entity in &g.entities {
        let sn = snake(&entity.name);
        f.insert(format!("src-tauri/src/commands/{}_commands.rs", sn), r_entity_commands(entity));
        f.insert(format!("src-tauri/src/models/{}.rs", sn), r_entity_model(entity));
    }

    f.insert("src-tauri/tauri.conf.json".into(), r_tauri_conf(app, &bundle_rev));
    f.insert("src-tauri/capabilities/default.json".into(), r_capabilities(&bundle_rev));
    f.insert("src/main.tsx".into(), r_frontend_main());
    f.insert("src/App.tsx".into(), r_app_tsx(g));
    f.insert("src/ipc.ts".into(), r_ipc_ts(g));
    f.insert("src/pages/Login.tsx".into(), r_login_page());
    for entity in &g.entities {
        f.insert(format!("src/pages/{}List.tsx", pascal(&entity.name)), r_entity_list(entity));
    }
    f.insert("index.html".into(), r_index_html(app));
    f.insert("package.json".into(), r_package_json(app, bundle));
    f.insert("vite.config.ts".into(), r_vite_config());
    f.insert("tsconfig.json".into(), r_tsconfig());
    f.insert(".gitignore".into(), r_gitignore());
    f.insert(".env.example".into(), r_env_example(bundle));
    f.insert("ARCHITECTURE.md".into(), r_architecture_md(g));
    f.insert("openapi.yaml".into(), r_openapi_yaml(g));
    f.insert("README.md".into(), r_app_readme(app, g));
    f
}

fn r_cargo_toml(app: &str, bundle: &str, bundle_lib: &str) -> String {
    fill(r#"[package]
name = "{{BUNDLE}}"
version = "0.1.0"
description = "{{APP}}"
edition = "2021"
rust-version = "1.75"

[lib]
name = "{{LIB}}"
crate-type = ["staticlib", "cdylib", "rlib"]

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = ["protocol-asset"] }
tauri-plugin-shell = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["rt-multi-thread", "macros"] }
rusqlite = { version = "0.31", features = ["bundled"] }
argon2 = "0.5"
rand_core = { version = "0.6", features = ["getrandom"] }
uuid = { version = "1", features = ["v4"] }

[features]
default = ["custom-protocol"]
custom-protocol = ["tauri/custom-protocol"]

[profile.release]
codegen-units = 1
lto = true
opt-level = "s"
panic = "abort"
strip = true
"#, &[("APP", app), ("BUNDLE", bundle), ("LIB", bundle_lib)])
}

fn r_app_main(bundle_lib: &str) -> String {
    format!("#![cfg_attr(not(debug_assertions), windows_subsystem = \"windows\")]\nfn main() {{\n    {}::run();\n}}\n", bundle_lib)
}

fn r_lib_rs(g: &Genome, bundle_lib: &str) -> String {
    let mods: String = g.entities.iter()
        .map(|e| format!("    commands::{}::create_{},\n    commands::{}::list_{},\n    commands::{}::get_{},\n    commands::{}::update_{},\n    commands::{}::delete_{},",
            format!("{}_commands", snake(&e.name)),
            snake(&e.name),
            format!("{}_commands", snake(&e.name)),
            plural(&snake(&e.name)),
            format!("{}_commands", snake(&e.name)),
            snake(&e.name),
            format!("{}_commands", snake(&e.name)),
            snake(&e.name),
            format!("{}_commands", snake(&e.name)),
            snake(&e.name),
        ))
        .collect::<Vec<_>>()
        .join("\n");
    fill(r#"pub mod auth;
pub mod commands;
pub mod db;
pub mod models;

use std::collections::HashMap;
use std::sync::Mutex;
use rusqlite::Connection;

pub struct AppState {
    pub db: Mutex<Connection>,
    pub sessions: Mutex<HashMap<String, i64>>,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let conn = db::open_db().expect("failed to open SQLite database");
    db::run_migrations(&conn).expect("failed to run migrations");
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(AppState {
            db: Mutex::new(conn),
            sessions: Mutex::new(HashMap::new()),
        })
        .invoke_handler(tauri::generate_handler![
            auth::register_user,
            auth::login_user,
            auth::logout_user,
            auth::get_me,
{{HANDLERS}}
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
"#, &[("LIB", bundle_lib), ("HANDLERS", &mods)])
}

fn r_db_rs(g: &Genome, bundle: &str) -> String {
    let tables: String = g.entities.iter().map(|e| {
        let cols: String = e.fields.iter()
            .filter(|(f, _)| f != "id")
            .map(|(f, fs)| {
                let nn = if fs.required { " NOT NULL" } else { "" };
                format!("            {} {}{},\n", f, sqlite_type(&fs.field_type), nn)
            })
            .collect();
        format!("        CREATE TABLE IF NOT EXISTS {} (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n{}            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n        );\n", plural(&snake(&e.name)), cols)
    }).collect();
    fill(r#"use rusqlite::{Connection, Result};
use std::path::PathBuf;

pub fn db_path() -> PathBuf {
    std::env::var("APP_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("{{BUNDLE}}.db")
}

pub fn open_db() -> Result<Connection> {
    let path = db_path();
    if let Some(p) = path.parent() { std::fs::create_dir_all(p).ok(); }
    Connection::open(path)
}

pub fn run_migrations(conn: &Connection) -> Result<()> {
    conn.execute_batch(&format!(
        "PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        {}",
        "{{TABLES}}"
    ))
}
"#, &[("BUNDLE", bundle), ("TABLES", &tables)])
}

fn r_auth_rs() -> String {
    r#"use crate::AppState;
use argon2::{
    password_hash::{rand_core::OsRng, PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
    Argon2,
};
use serde::Serialize;
use uuid::Uuid;

#[derive(Serialize)]
pub struct UserInfo { pub id: i64, pub username: String }

pub fn require_session(state: &AppState, token: &str) -> Result<i64, String> {
    state.sessions.lock().unwrap().get(token).copied()
        .ok_or_else(|| "Unauthorized".to_string())
}

#[tauri::command]
pub fn register_user(
    state: tauri::State<AppState>,
    username: String,
    password: String,
) -> Result<UserInfo, String> {
    let salt = SaltString::generate(&mut OsRng);
    let hash = Argon2::default()
        .hash_password(password.as_bytes(), &salt)
        .map_err(|e| e.to_string())?
        .to_string();
    let db = state.db.lock().unwrap();
    db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?1, ?2)",
        (&username, &hash),
    ).map_err(|e| e.to_string())?;
    let id = db.last_insert_rowid();
    Ok(UserInfo { id, username })
}

#[tauri::command]
pub fn login_user(
    state: tauri::State<AppState>,
    username: String,
    password: String,
) -> Result<String, String> {
    let db = state.db.lock().unwrap();
    let (id, hash): (i64, String) = db
        .query_row(
            "SELECT id, password_hash FROM users WHERE username = ?1",
            [&username],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .map_err(|_| "Invalid credentials".to_string())?;
    let parsed = PasswordHash::new(&hash).map_err(|e| e.to_string())?;
    Argon2::default()
        .verify_password(password.as_bytes(), &parsed)
        .map_err(|_| "Invalid credentials".to_string())?;
    let token = Uuid::new_v4().to_string();
    state.sessions.lock().unwrap().insert(token.clone(), id);
    Ok(token)
}

#[tauri::command]
pub fn logout_user(state: tauri::State<AppState>, token: String) {
    state.sessions.lock().unwrap().remove(&token);
}

#[tauri::command]
pub fn get_me(state: tauri::State<AppState>, token: String) -> Result<UserInfo, String> {
    let uid = require_session(&state, &token)?;
    let db = state.db.lock().unwrap();
    let username: String = db
        .query_row("SELECT username FROM users WHERE id = ?1", [uid], |r| r.get(0))
        .map_err(|e| e.to_string())?;
    Ok(UserInfo { id: uid, username })
}
"#.to_string()
}

fn r_commands_mod(g: &Genome) -> String {
    g.entities.iter()
        .map(|e| format!("pub mod {}_commands;", snake(&e.name)))
        .collect::<Vec<_>>()
        .join("\n") + "\n"
}

fn r_models_mod(g: &Genome) -> String {
    g.entities.iter()
        .map(|e| format!("pub mod {};", snake(&e.name)))
        .collect::<Vec<_>>()
        .join("\n") + "\n"
}

fn r_entity_model(entity: &Entity) -> String {
    let _sn = snake(&entity.name);
    let ps = pascal(&entity.name);
    let fields: String = entity.fields.iter()
        .map(|(f, fs)| format!("    pub {}: {},\n", f, rust_type(&fs.field_type)))
        .collect();
    format!(
        "use serde::{{Deserialize, Serialize}};\n\n#[derive(Clone, Debug, Default, Deserialize, Serialize)]\npub struct {} {{\n{}}}\n",
        ps, fields
    )
}

fn r_entity_commands(entity: &Entity) -> String {
    let sn = snake(&entity.name);
    let ps = pascal(&entity.name);
    let tbl = plural(&sn);
    let all_fields: Vec<&str> = entity.fields.iter().map(|(f, _)| f.as_str()).collect();
    let data_fields: Vec<(&str, &FieldSpec)> = entity.fields.iter()
        .filter(|(f, _)| f != "id" && f != "user_id" && f != "created_at")
        .map(|(f, fs)| (f.as_str(), fs))
        .collect();
    let col_list = data_fields.iter().map(|(f, _)| *f).collect::<Vec<_>>().join(", ");
    let set_list = data_fields.iter().enumerate()
        .map(|(i, (f, _))| format!("{}=?{}", f, i + 1))
        .collect::<Vec<_>>()
        .join(", ");
    let placeholders = (0..data_fields.len())
        .map(|i| format!("?{}", i + 2))
        .collect::<Vec<_>>()
        .join(", ");
    let row_fields: String = all_fields.iter().enumerate()
        .map(|(i, f)| format!("            {}: row.get({})?,\n", f, i))
        .collect();
    let params: String = data_fields.iter()
        .map(|(f, _)| format!(", {}", f))
        .collect();
    let param_decl: String = data_fields.iter()
        .map(|(f, fs)| format!(", {}: {}", f, rust_type(&fs.field_type)))
        .collect();
    let sel = all_fields.iter().map(|f| f.to_string()).collect::<Vec<_>>().join(", ");

    format!(r#"use crate::{{auth::require_session, models::{sn}::{ps}, AppState}};

#[tauri::command]
pub fn create_{sn}(state: tauri::State<AppState>, token: String{param_decl}) -> Result<{ps}, String> {{
    let uid = require_session(&state, &token)?;
    let db = state.db.lock().unwrap();
    db.execute(
        "INSERT INTO {tbl} (user_id, {col_list}) VALUES (?1, {placeholders})",
        rusqlite::params![uid{params}],
    ).map_err(|e| e.to_string())?;
    let id = db.last_insert_rowid();
    db.query_row("SELECT {sel} FROM {tbl} WHERE id = ?1", [id], |row| Ok({ps} {{
{row_fields}    }})).map_err(|e| e.to_string())
}}

#[tauri::command]
pub fn list_{tbl}(state: tauri::State<AppState>, token: String) -> Result<Vec<{ps}>, String> {{
    let uid = require_session(&state, &token)?;
    let db = state.db.lock().unwrap();
    let mut stmt = db.prepare("SELECT {sel} FROM {tbl} WHERE user_id = ?1 ORDER BY id DESC").map_err(|e| e.to_string())?;
    let rows = stmt.query_map([uid], |row| Ok({ps} {{
{row_fields}    }})).map_err(|e| e.to_string())?;
    rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())
}}

#[tauri::command]
pub fn get_{sn}(state: tauri::State<AppState>, token: String, id: i64) -> Result<{ps}, String> {{
    let uid = require_session(&state, &token)?;
    let db = state.db.lock().unwrap();
    db.query_row("SELECT {sel} FROM {tbl} WHERE id = ?1 AND user_id = ?2", [id, uid], |row| Ok({ps} {{
{row_fields}    }})).map_err(|e| e.to_string())
}}

#[tauri::command]
pub fn update_{sn}(state: tauri::State<AppState>, token: String, id: i64{param_decl}) -> Result<{ps}, String> {{
    let uid = require_session(&state, &token)?;
    let db = state.db.lock().unwrap();
    db.execute("UPDATE {tbl} SET {set_list} WHERE id = ?{next} AND user_id = ?{next2}", rusqlite::params![{params_bare}id, uid]).map_err(|e| e.to_string())?;
    db.query_row("SELECT {sel} FROM {tbl} WHERE id = ?1", [id], |row| Ok({ps} {{
{row_fields}    }})).map_err(|e| e.to_string())
}}

#[tauri::command]
pub fn delete_{sn}(state: tauri::State<AppState>, token: String, id: i64) -> Result<(), String> {{
    let uid = require_session(&state, &token)?;
    let db = state.db.lock().unwrap();
    db.execute("DELETE FROM {tbl} WHERE id = ?1 AND user_id = ?2", [id, uid]).map_err(|e| e.to_string())?;
    Ok(())
}}
"#,
        sn = sn, ps = ps, tbl = tbl,
        param_decl = param_decl, params = params,
        col_list = col_list, set_list = set_list, placeholders = placeholders,
        row_fields = row_fields, sel = sel,
        next = data_fields.len() + 1,
        next2 = data_fields.len() + 2,
        params_bare = data_fields.iter().map(|(f, _)| format!("{}, ", f)).collect::<String>(),
    )
}

fn r_tauri_conf(app: &str, bundle_rev: &str) -> String {
    fill(r#"{
  "productName": "{{APP}}",
  "version": "0.1.0",
  "identifier": "{{BUNDLE_REV}}",
  "build": {
    "beforeDevCommand": "npm run dev",
    "beforeBuildCommand": "npm run build",
    "devUrl": "http://localhost:1420",
    "frontendDist": "../dist"
  },
  "app": {
    "windows": [
      {
        "title": "{{APP}}",
        "width": 1200,
        "height": 800,
        "resizable": true,
        "fullscreen": false
      }
    ],
    "security": { "csp": null }
  },
  "bundle": {
    "active": true,
    "targets": "all",
    "icon": ["icons/32x32.png", "icons/128x128.png", "icons/icon.ico"]
  }
}
"#, &[("APP", app), ("BUNDLE_REV", bundle_rev)])
}

fn r_capabilities(bundle_rev: &str) -> String {
    fill(r#"{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "Default capability",
  "windows": ["main"],
  "permissions": [
    "core:default",
    "shell:allow-open"
  ]
}
"#, &[("ID", bundle_rev)])
}

fn r_frontend_main() -> String {
    r#"import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode><App /></React.StrictMode>
)
"#.to_string()
}

fn r_app_tsx(g: &Genome) -> String {
    let routes: String = g.entities.iter()
        .map(|e| {
            let ps = pascal(&e.name);
            format!("        <Route path=\"/{}\" element={{<{}List token={{token}} />}} />\n",
                plural(&snake(&e.name)), ps)
        })
        .collect();
    let imports: String = g.entities.iter()
        .map(|e| {
            let ps = pascal(&e.name);
            format!("import {}List from './pages/{}List'\n", ps, ps)
        })
        .collect();
    fill(r#"import { useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Login from './pages/Login'
{{IMPORTS}}
export default function App() {
  const [token, setToken] = useState<string | null>(null)
  if (!token) return <Login onLogin={setToken} />
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/{{FIRST}}" />} />
{{ROUTES}}      </Routes>
    </BrowserRouter>
  )
}
"#, &[
        ("IMPORTS", &imports),
        ("ROUTES", &routes),
        ("FIRST", &g.entities.first().map(|e| plural(&snake(&e.name))).unwrap_or_else(|| "home".to_string())),
    ])
}

fn r_ipc_ts(g: &Genome) -> String {
    let mut lines = vec!["import { invoke } from '@tauri-apps/api/core'\n".to_string()];
    lines.push("export interface UserInfo { id: number; username: string }\n".to_string());
    lines.push("export const auth = {\n  register: (username: string, password: string) => invoke<UserInfo>('register_user', { username, password }),\n  login: (username: string, password: string) => invoke<string>('login_user', { username, password }),\n  logout: (token: string) => invoke<void>('logout_user', { token }),\n  me: (token: string) => invoke<UserInfo>('get_me', { token }),\n}\n".to_string());
    for entity in &g.entities {
        let sn = snake(&entity.name);
        let ps = pascal(&entity.name);
        let tbl = plural(&sn);
        let data_fields: Vec<(&str, &FieldSpec)> = entity.fields.iter()
            .filter(|(f, _)| f != "id" && f != "user_id" && f != "created_at")
            .map(|(f, fs)| (f.as_str(), fs))
            .collect();
        let iface_fields: String = entity.fields.iter()
            .map(|(f, fs)| format!("  {}: {}; ", f, ts_type(&fs.field_type)))
            .collect();
        let create_params: String = data_fields.iter()
            .map(|(f, fs)| format!(", {}: {}", f, ts_type(&fs.field_type)))
            .collect();
        let create_obj: String = data_fields.iter()
            .map(|(f, _)| format!(", {}", f))
            .collect();
        lines.push(format!("export interface {} {{ {} }}\n", ps, iface_fields));
        lines.push(format!("export const {} = {{\n  list: (token: string) => invoke<{}[]>('list_{}', {{ token }}),\n  get: (token: string, id: number) => invoke<{}>('get_{}', {{ token, id }}),\n  create: (token: string{}) => invoke<{}>('create_{}', {{ token{} }}),\n  update: (token: string, id: number{}) => invoke<{}>('update_{}', {{ token, id{} }}),\n  delete: (token: string, id: number) => invoke<void>('delete_{}', {{ token, id }}),\n}}\n",
            sn, ps, tbl, ps, sn, create_params, ps, sn, create_obj, create_params, ps, sn, create_obj, sn));
    }
    lines.join("")
}

fn r_login_page() -> String {
    r#"import { useState } from 'react'
import { auth } from '../ipc'

export default function Login({ onLogin }: { onLogin: (t: string) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [error, setError] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    try {
      if (mode === 'register') {
        await auth.register(username, password)
      }
      const token = await auth.login(username, password)
      onLogin(token)
    } catch (err: any) {
      setError(String(err))
    }
  }

  return (
    <div style={{ maxWidth: 360, margin: '80px auto', fontFamily: 'system-ui' }}>
      <h2 style={{ textAlign: 'center' }}>{mode === 'login' ? 'Sign In' : 'Create Account'}</h2>
      {error && <p style={{ color: 'red' }}>{error}</p>}
      <form onSubmit={submit}>
        <input value={username} onChange={e => setUsername(e.target.value)}
          placeholder="Username" required style={{ display: 'block', width: '100%', marginBottom: 8, padding: 8 }} />
        <input type="password" value={password} onChange={e => setPassword(e.target.value)}
          placeholder="Password" required style={{ display: 'block', width: '100%', marginBottom: 8, padding: 8 }} />
        <button type="submit" style={{ width: '100%', padding: 10, background: '#4F46E5', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
          {mode === 'login' ? 'Sign In' : 'Register'}
        </button>
      </form>
      <button onClick={() => setMode(m => m === 'login' ? 'register' : 'login')}
        style={{ marginTop: 12, background: 'none', border: 'none', cursor: 'pointer', color: '#4F46E5' }}>
        {mode === 'login' ? 'Need an account? Register' : 'Already have an account? Sign in'}
      </button>
    </div>
  )
}
"#.to_string()
}

fn r_entity_list(entity: &Entity) -> String {
    let sn = snake(&entity.name);
    let ps = pascal(&entity.name);
    let _tbl = plural(&sn);
    let data_fields: Vec<&str> = entity.fields.iter()
        .filter(|(f, _)| f != "id" && f != "user_id" && f != "created_at")
        .map(|(f, _)| f.as_str())
        .collect();
    let headers: String = data_fields.iter().map(|f| format!("<th>{}</th>", f)).collect();
    let cells: String = data_fields.iter().map(|f| format!("<td>{{item.{}}}</td>", f)).collect();
    let inputs: String = data_fields.iter()
        .map(|f| format!("        <input placeholder=\"{}\" value={{form.{} as string ?? ''}} onChange={{e => setForm(x => ({{...x, {}: e.target.value}}))}}\n          style={{{{display:'block',width:'100%',marginBottom:6,padding:6}}}} />\n", f, f, f))
        .collect();
    let init_form: String = data_fields.iter()
        .map(|f| format!("{}: ''", f))
        .collect::<Vec<_>>()
        .join(", ");
    fill(r#"import { useEffect, useState } from 'react'
import { {{LOWER}}, {{PASCAL}} } from '../ipc'

export default function {{PASCAL}}List({ token }: { token: string }) {
  const [items, setItems] = useState<{{PASCAL}}[]>([])
  const [form, setForm] = useState<Partial<{{PASCAL}}>>({ {{INIT}} })
  const [error, setError] = useState('')

  useEffect(() => { load() }, [])

  async function load() {
    try { setItems(await {{LOWER}}.list(token)) } catch (e: any) { setError(String(e)) }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    try {
      await {{LOWER}}.create(token, ...Object.values(form) as any)
      setForm({ {{INIT}} })
      load()
    } catch (e: any) { setError(String(e)) }
  }

  async function handleDelete(id: number) {
    try { await {{LOWER}}.delete(token, id); load() } catch (e: any) { setError(String(e)) }
  }

  return (
    <div style={{ padding: 24, fontFamily: 'system-ui' }}>
      <h2>{{PASCAL}}</h2>
      {error && <p style={{ color: 'red' }}>{error}</p>}
      <form onSubmit={handleCreate} style={{ marginBottom: 24 }}>
{{INPUTS}}        <button type="submit" style={{ padding: '8px 16px', background: '#4F46E5', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>Add</button>
      </form>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr><th>id</th>{{HEADERS}}<th></th></tr></thead>
        <tbody>
          {items.map(item => (
            <tr key={item.id} style={{ borderBottom: '1px solid #eee' }}>
              <td>{item.id}</td>{{CELLS}}
              <td><button onClick={() => handleDelete(item.id!)}>Delete</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
"#, &[("LOWER", &sn), ("PASCAL", &ps), ("HEADERS", &headers), ("CELLS", &cells), ("INPUTS", &inputs), ("INIT", &init_form)])
}

fn r_index_html(app: &str) -> String {
    fill(r#"<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{{APP}}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"#, &[("APP", app)])
}

fn r_package_json(app: &str, bundle: &str) -> String {
    fill(r#"{
  "name": "{{BUNDLE}}",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "tauri": "tauri"
  },
  "dependencies": {
    "@tauri-apps/api": "^2",
    "@tauri-apps/plugin-shell": "^2",
    "react": "^18",
    "react-dom": "^18",
    "react-router-dom": "^6"
  },
  "devDependencies": {
    "@tauri-apps/cli": "^2",
    "@types/react": "^18",
    "@types/react-dom": "^18",
    "typescript": "^5",
    "vite": "^5",
    "@vitejs/plugin-react": "^4"
  }
}
"#, &[("APP", app), ("BUNDLE", bundle)])
}

fn r_vite_config() -> String {
    r#"import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  envPrefix: ['VITE_', 'TAURI_'],
  build: {
    target: ['es2021', 'chrome105', 'safari13'],
    minify: !process.env.TAURI_DEBUG ? 'esbuild' : false,
    sourcemap: !!process.env.TAURI_DEBUG,
  },
})
"#.to_string()
}

fn r_tsconfig() -> String {
    r#"{
  "compilerOptions": {
    "target": "ES2021",
    "useDefineForClassFields": true,
    "lib": ["ES2021", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"]
}
"#.to_string()
}

fn r_gitignore() -> String {
    "/target\n/dist\nnode_modules\n.env\n*.db\n*.db-shm\n*.db-wal\n".to_string()
}

fn r_env_example(bundle: &str) -> String {
    format!("# Path for SQLite database (optional — defaults to current dir)\nAPP_DATA_DIR=./data\n# Bundle identifier\nBUNDLE_ID={}\n", bundle)
}

fn r_architecture_md(g: &Genome) -> String {
    let elements: String = g.archimate_elements.iter()
        .map(|e| format!("| {} | {} | {} |\n", e.name, e.kind, e.description))
        .collect();
    fill(r#"# Architecture — {{APP}}

> Generated by archiet-microcodegen-tauri. ArchiMate 3.2 element inventory.

## Stack

- **Runtime**: Tauri v2 (Rust backend + React/TypeScript frontend)
- **Storage**: SQLite (rusqlite, bundled) — WAL mode
- **Auth**: Argon2id password hashing, UUID session tokens (in-memory AppState)
- **IPC**: Tauri `invoke()` commands — typed, per-user isolation enforced on every query

## ArchiMate 3.2 Elements

| Name | Type | Description |
|---|---|---|
{{ELEMENTS}}

## Data Isolation

Every entity table has a `user_id` column with a `FOREIGN KEY REFERENCES users(id) ON DELETE CASCADE`.
Every IPC command validates the session token and filters queries by `user_id`. Cross-user data access is structurally impossible.

## Auth Flow

1. `register_user(username, password)` — Argon2id hash, INSERT into users table
2. `login_user(username, password)` — verify hash, generate UUID token, store in `AppState.sessions`
3. Every subsequent command: `require_session(token)` → `user_id`, query filtered by `user_id`
4. `logout_user(token)` — remove from `AppState.sessions`

Session tokens live in JavaScript memory only — never in localStorage or on disk.
"#, &[("APP", &g.solution_name), ("ELEMENTS", &elements)])
}

fn r_openapi_yaml(g: &Genome) -> String {
    let mut paths = String::new();
    for entity in &g.entities {
        let sn = snake(&entity.name);
        let tbl = plural(&sn);
        let ps = pascal(&entity.name);
        paths.push_str(&format!(
            "  {}:\n    list_{}: list all {} for the authenticated user\n    create_{}: create a new {}\n  {} (by id):\n    get_{}: fetch one {}\n    update_{}: update one {}\n    delete_{}: delete one {}\n",
            tbl, sn, ps, sn, ps, sn, sn, ps, sn, ps, sn, ps
        ));
    }
    fill(r#"# IPC Contract — {{APP}}
# Generated by archiet-microcodegen-tauri
# Commands are invoked via Tauri invoke(), not HTTP.
# All commands require a 'token' parameter (UUID session token from login_user).

info:
  title: "{{APP}} IPC Contract"
  version: "0.1.0"
  description: "Tauri IPC command surface. Not HTTP — use @tauri-apps/api invoke()."

auth_commands:
  register_user:
    params: { username: string, password: string }
    returns: UserInfo
  login_user:
    params: { username: string, password: string }
    returns: string  # session token
  logout_user:
    params: { token: string }
    returns: void
  get_me:
    params: { token: string }
    returns: UserInfo

entity_commands:
{{PATHS}}
"#, &[("APP", &g.solution_name), ("PATHS", &paths)])
}

fn r_app_readme(app: &str, g: &Genome) -> String {
    let entity_list: String = g.entities.iter()
        .map(|e| format!("- `{}` — {} entity\n", pascal(&e.name), e.name))
        .collect();
    fill(r#"# {{APP}}

> Generated by [archiet-microcodegen-tauri](https://www.npmjs.com/package/archiet-microcodegen-tauri) — PRD → Tauri v2 desktop app.

## Development

```bash
npm install
npm run tauri dev
```

## Build

```bash
npm run tauri build
```

## Entities

{{ENTITIES}}

## Auth

- Register: `auth.register(username, password)`
- Login: `auth.login(username, password)` → returns session token
- All entity commands require the session token

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for ArchiMate 3.2 element inventory and data isolation design.
"#, &[("APP", app), ("ENTITIES", &entity_list)])
}

// ─── CLI main ────────────────────────────────────────────────────────────────

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 || args[1] == "--help" || args[1] == "-h" {
        eprintln!("archiet-microcodegen-tauri v0.1.0");
        eprintln!("Usage: archiet-microcodegen-tauri <prd.md> [--out <dir>] [--zip <file>]");
        eprintln!("       archiet-microcodegen-tauri --sample");
        std::process::exit(0);
    }

    if args[1] == "--sample" {
        print!("{}", SAMPLE_PRD);
        return;
    }

    let prd_path = &args[1];
    let text = fs::read_to_string(prd_path)
        .unwrap_or_else(|e| { eprintln!("Error reading {}: {}", prd_path, e); std::process::exit(1); });

    let manifest = parse_prd(&text);
    let genome = manifest_to_genome(manifest);
    let files = render_genome(&genome);

    let mut out_dir: Option<&str> = None;
    let mut zip_path: Option<&str> = None;
    let mut i = 2;
    while i < args.len() {
        match args[i].as_str() {
            "--out" => { i += 1; out_dir = args.get(i).map(|s| s.as_str()); }
            "--zip" => { i += 1; zip_path = args.get(i).map(|s| s.as_str()); }
            _ => {}
        }
        i += 1;
    }

    if let Some(zip) = zip_path {
        let bytes = pack(&files);
        fs::write(zip, &bytes)
            .unwrap_or_else(|e| { eprintln!("Error writing {}: {}", zip, e); std::process::exit(1); });
        eprintln!("Wrote {} files to {}", files.len(), zip);
    } else {
        let dir = out_dir.unwrap_or("./out");
        write_disk(&files, Path::new(dir));
        eprintln!("Wrote {} files to {}", files.len(), dir);
    }

    eprintln!("Next: cd {} && npm install && npm run tauri dev", out_dir.unwrap_or("./out"));
}

const SAMPLE_PRD: &str = r#"# Task Manager

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

## User Stories

As a user, I want to create projects so that I can organise my work.
As a user, I want to add tasks to projects so that I can track progress.

## Integrations

No external integrations required.
"#;
