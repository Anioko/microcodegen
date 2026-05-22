#!/usr/bin/env python3
"""microcodegen.py — Archiet's core algorithm in one file.

PRD text → manifest → genome → rendered Flask app → ZIP bytes.

Contract:  microcodegen(prd_text) → bytes  (working, bootable Flask ZIP)

  # CLI:  python scripts/microcodegen.py prd.md > app.zip
  #       python scripts/microcodegen.py prd.md --out /tmp/myapp/
  # Lib:  from scripts.microcodegen import microcodegen

Stages:
  1. parse_prd(text) → manifest dict   (regex extraction, no LLM)
  2. manifest_to_genome(manifest) → genome dict
  3. render_genome(genome) → {path: content}   (String.Template, Flask only)
  4. pack(files) → bytes   (stdlib zipfile)

NOT: LLM extraction, multi-stack, capability emission, frontend, stub-fill,
     quality scoring, rate limiting, observability, secret rotation.

Why this file exists:
  ONBOARDING  — grasps the algorithm in 10 min; codegen_service.py is efficiency on top.
  REGRESSION  — bug that doesn't repro here is in efficiency layers, not the algorithm.
  KARPATHY BAR — if we can't express the core in <700 LOC, we're hiding behind layers.
  SPEC        — algorithmic changes must update this file; efficiency changes needn't.

Constraints:
  - Pure stdlib; zero app.* / agents.* / templates/ imports.
  - No TODOs — handle it or scope it out explicitly.
  - Hard ceiling: 700 LOC (enforced by tests/test_microcodegen.py).
"""

from __future__ import annotations

import argparse
import io
import json
import re
import secrets
import string
import sys
import zipfile
from pathlib import Path

# ─── STAGE 1 ────────────────────────────────────────────────────────────────
# parse_prd(text) → manifest dict.
#
# Pure regex + heuristic extraction. The full pipeline uses a chunked LLM
# extractor with overlap + dedup merge; this version does pattern matching.
# It will miss subtle PRDs. That's acceptable for a spec-grade reference.


_ENTITY_PATTERN = re.compile(
    # Matches "## Entities" or "Entities:" or "## Data Models" — header
    # introducing an entity section.
    r"^#{1,3}\s*(?:entities|data models|domain models|entity list)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_ENTITY_NAME_PATTERN = re.compile(
    # Matches "- Order" / "* User" / "## Order" — an entity name in a list
    # or sub-header. Captures the name only (no fields yet).
    # Tolerates markdown bold (**Order**) which customers write naturally,
    # and tolerates a trailing space (so "- Order with description" still
    # matches "Order"). We deliberately use [ \t] (NOT \s) for the terminator
    # so the match cannot cross a newline into field declarations and drop
    # the first field of every entity.
    r"^[\s\-\*\#]+\*{0,2}([A-Z][a-zA-Z0-9_]{1,40})\*{0,2}[ \t]*(?::|—|-|[ \t]|$)",
    re.MULTILINE,
)

_FIELD_PATTERN = re.compile(
    # Matches "- name: string" / "* email: text (required)" — a field on an
    # entity. Captures field name + type. Modifiers like "required" are
    # parsed by _parse_field_modifiers below.
    r"^[\s\-\*]+([a-z_][a-z0-9_]{0,40})\s*[:—-]\s*([a-zA-Z]+)([^\n]*)",
    re.MULTILINE,
)

_INLINE_FIELD_PATTERN = re.compile(
    # Matches "name (string, required)" / "email (text)" — a field declared
    # inline on the entity-name line. Customers write entities this way:
    #   - Project: name (string, required), description (text), status (string)
    # The inline path runs as a fallback when sub-bullet fields yield 0.
    r"([a-z_][a-z0-9_]{0,40})\s*\(\s*([a-zA-Z]+)([^)]*)\)",
)

_USER_STORY_PATTERN = re.compile(
    # Matches "As a X, I want Y so that Z." — the canonical user story shape.
    # Captures the role, want, and so-that clauses for direct AC derivation.
    r"As\s+(?:a|an)\s+([^,]+?),\s+I\s+want\s+(?:to\s+)?([^,]+?)(?:,?\s*so\s+that\s+([^.]+))?\.",
    re.IGNORECASE,
)

_INTEGRATION_KEYWORDS = {
    # Maps customer-mentioned vendor → integration spec the genome accepts.
    # When the PRD mentions any of these strings, we add a corresponding
    # entry to genome.integrations[]. The full pipeline does broader
    # detection; this list is intentionally conservative.
    "stripe": {"name": "stripe", "category": "payments"},
    "auth0": {"name": "auth0", "category": "auth"},
    "clerk": {"name": "clerk", "category": "auth"},
    "supabase": {"name": "supabase", "category": "auth"},
    "sendgrid": {"name": "sendgrid", "category": "email"},
    "postmark": {"name": "postmark", "category": "email"},
    "twilio": {"name": "twilio", "category": "sms"},
    "datadog": {"name": "datadog", "category": "observability"},
    "segment": {"name": "segment", "category": "analytics"},
}


def _parse_field_modifiers(modifier_text: str) -> dict:
    """Extract 'required', 'unique', 'indexed' flags from "(required, unique)".

    The full pipeline tolerates dozens of modifier spellings; this version
    handles the three most-common ones literally.
    """
    flags = {"required": False, "unique": False, "indexed": False}
    text = modifier_text.lower()
    if "required" in text or "not null" in text or " ! " in text:
        flags["required"] = True
    if "unique" in text:
        flags["unique"] = True
    if "indexed" in text or "index" in text:
        flags["indexed"] = True
    return flags


def _solution_name_from_prd(text: str) -> str:
    """Pull the first H1 (# Title) as the solution name; fall back to a default."""
    m = re.match(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return "Generated App"


def parse_prd(text: str) -> dict:
    """Extract a manifest dict from raw PRD text.

    Returns shape:
      {
        "solution_name": str,
        "entities": [{"name": str, "fields": [{"name", "type", "required",
                                                "unique", "indexed"}]}],
        "user_stories": [{"as_a": str, "i_want": str, "so_that": str}],
        "integrations": [{"name": str, "category": str}],
      }
    """
    solution_name = _solution_name_from_prd(text)

    # Entities: find the section header, then extract names until the next
    # H1/H2 header. If no entity section, skip — empty manifest is allowed.
    entities: list[dict] = []
    section_match = _ENTITY_PATTERN.search(text)
    entity_section = ""
    if section_match:
        # Read from the section start to the next top-level header (or EOF)
        start = section_match.end()
        next_header = re.search(r"^#{1,2}\s+\S", text[start:], re.MULTILINE)
        end = start + next_header.start() if next_header else len(text)
        entity_section = text[start:end]

    seen: set[str] = set()
    for m in _ENTITY_NAME_PATTERN.finditer(entity_section):
        ename = m.group(1)
        if ename in seen:
            continue
        seen.add(ename)
        # Look at the lines following this entity name (until next entity
        # name or end of section) for field declarations.
        ent_start = m.end()
        next_entity = _ENTITY_NAME_PATTERN.search(entity_section, ent_start)
        ent_end = next_entity.start() if next_entity else len(entity_section)
        ent_body = entity_section[ent_start:ent_end]

        fields: list[dict] = []
        seen_fields: set[str] = set()
        for fm in _FIELD_PATTERN.finditer(ent_body):
            fname, ftype, modifier_text = fm.group(1), fm.group(2), fm.group(3)
            # Skip the entity-name line that the entity-name regex
            # already consumed (our patterns can overlap on indent).
            if fname in seen:
                continue
            if fname in seen_fields:
                continue
            seen_fields.add(fname)
            flags = _parse_field_modifiers(modifier_text)
            fields.append(
                {
                    "name": fname,
                    "type": ftype.lower(),
                    **flags,
                }
            )

        # Fallback: scan the entity-name line itself for inline "field (type)"
        # patterns. Customers naturally write inline-formatted entity rows.
        if not fields:
            entity_name_line = (
                entity_section[m.start() : m.end()] + ent_body.split("\n", 1)[0]
            )
            for im in _INLINE_FIELD_PATTERN.finditer(entity_name_line):
                fname, ftype, modifier_text = im.group(1), im.group(2), im.group(3)
                if fname in seen_fields:
                    continue
                seen_fields.add(fname)
                flags = _parse_field_modifiers(modifier_text)
                fields.append(
                    {
                        "name": fname,
                        "type": ftype.lower(),
                        **flags,
                    }
                )

        entities.append({"name": ename, "fields": fields})

    # User stories: scan the whole document.
    stories = []
    for m in _USER_STORY_PATTERN.finditer(text):
        stories.append(
            {
                "as_a": (m.group(1) or "").strip(),
                "i_want": (m.group(2) or "").strip(),
                "so_that": (m.group(3) or "").strip(),
            }
        )

    # Integrations: substring match on known vendor names.
    integrations = []
    text_lower = text.lower()
    for vendor, spec in _INTEGRATION_KEYWORDS.items():
        if vendor in text_lower:
            integrations.append(spec)

    return {
        "solution_name": solution_name,
        "entities": entities,
        "user_stories": stories,
        "integrations": integrations,
    }


# ─── STAGE 2 ────────────────────────────────────────────────────────────────
# manifest_to_genome(manifest) → genome dict.
#
# Maps the heuristic manifest into the canonical genome shape that
# render_genome consumes. Single function, no fallbacks.


def _snake(s: str) -> str:
    """Convert "Order Line" → "order_line". The full pipeline has 10+
    snake-case implementations across modules; this is the canonical one."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.strip()).strip("_")
    s = re.sub(r"([a-z])([A-Z])", r"\1_\2", s)
    return s.lower()


def manifest_to_genome(manifest: dict) -> dict:
    """Map the heuristic manifest into the canonical genome shape.

    The genome is the load-bearing IR. All downstream stages read from
    here. The full pipeline supports realtime primitives, capabilities,
    workflows, screens, etc.; this version emits only modules + entities.
    """
    name = manifest["solution_name"]
    snake_name = _snake(name)

    # Entities → modules.core.entities.<EntityName>
    entities_dict: dict[str, dict] = {}
    for ent in manifest.get("entities", []):
        # The genome shape uses field-dict-of-dict, not field-list. Convert.
        fields: dict[str, dict] = {"id": {"type": "uuid", "required": True}}
        for f in ent.get("fields", []):
            if f["name"] in ("id", "created_at", "updated_at"):
                continue  # auto-emitted by the rendering layer
            fields[f["name"]] = {
                "type": f["type"],
                "required": f["required"],
                "unique": f["unique"],
                "indexed": f["indexed"],
            }
        entities_dict[ent["name"]] = {
            "fields": fields,
            "description": f"{ent['name']} entity (generated by microcodegen)",
        }

    return {
        "genome_version": "1.0.0",
        "solution_id": 0,
        "solution_name": name,
        "bundle_id": snake_name,
        "language": "flask-nextjs",
        "modules": {
            "core": {
                "module_type": "crud",
                "description": "Core entities",
                "entities": entities_dict,
            },
        },
        "user_stories": manifest.get("user_stories", []),
        "integrations": manifest.get("integrations", []),
    }


# ─── STAGE 3 ────────────────────────────────────────────────────────────────
# render_genome(genome) → {path: content}.
#
# Inline templates via string.Template. Flask only. The full pipeline
# branches across 12 stacks via the StackRenderer dispatch; this version
# is single-stack by design — adding a stack here would violate the
# "atomic algorithm" contract.


_FILE_TEMPLATES: dict[str, string.Template] = {
    "requirements.txt": string.Template("""\
flask>=3.0
flask-sqlalchemy>=3.1
flask-jwt-extended>=4.6
flask-cors>=4.0
flask-migrate>=4.0
psycopg2-binary>=2.9
gunicorn>=22.0
python-dotenv>=1.0
"""),
    "wsgi.py": string.Template("""\
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
"""),
    "config.py": string.Template("""\
import os


def _required_secret(name: str) -> str:
    # Read a secret from env. Fail loudly if the customer forgot to set it.
    # Silent fallback to a literal 'change-me' would let anyone forge tokens
    # against the deployed app.
    value = os.environ.get(name)
    if not value or "change-me" in value.lower():
        raise RuntimeError(
            f"{name} is not set (or still set to a 'change-me' placeholder). "
            f"Set a real value in .env or your deploy environment before "
            f"running the app. The shipped .env.example has a freshly-"
            f"generated random value you can use."
        )
    return value


class Config:
    SECRET_KEY = _required_secret("SECRET_KEY")
    JWT_SECRET_KEY = _required_secret("JWT_SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "postgresql://archiet:archiet@localhost:5432/$bundle_id"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_TOKEN_LOCATION = ["cookies"]
    JWT_COOKIE_SECURE = os.environ.get("FLASK_ENV") == "production"
    JWT_COOKIE_SAMESITE = "Lax"
    JWT_COOKIE_HTTPONLY = True
    # CSRF protection is OFF by default so the README's curl quickstart works
    # end-to-end without the client having to grab and forward a CSRF token.
    # PRODUCTION CUSTOMERS: set this to True in your deploy env and update your
    # frontend to send the X-CSRF-TOKEN header on every state-changing request.
    JWT_COOKIE_CSRF_PROTECT = os.environ.get("JWT_COOKIE_CSRF_PROTECT", "false").lower() == "true"
"""),
    "app/__init__.py": string.Template("""\
import os

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate

from app.database import db
from config import Config


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.from_object(config_class)

    db.init_app(app)
    Migrate(app, db)
    JWTManager(app)
    # CORS allowlist — open to FRONTEND_URL only (NOT wildcard). Wildcard +
    # credentials would let any site call the API on behalf of the user.
    _frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
    CORS(app, origins=[_frontend_url], supports_credentials=True)

    # Auth (always emitted — every entity route is @jwt_required and a
    # customer cannot use the API without a login route to get a JWT cookie).
    from app.blueprints.auth_bp import auth_bp
    app.register_blueprint(auth_bp, url_prefix="/api/auth")

    # Blueprints
$blueprint_imports
$blueprint_registrations
    @app.get("/")
    def _root():
        # Serve the single-page demo. Customer can open http://localhost:5000
        # and click through register/login/CRUD without writing a frontend.
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "version": "$bundle_id"})

    # JSON error handlers — Flask's defaults return HTML, which breaks
    # frontends and SDKs that expect a JSON content-type on every API call.
    @app.errorhandler(400)
    def _err_400(_):
        return jsonify({"error": "bad request"}), 400

    @app.errorhandler(401)
    def _err_401(_):
        return jsonify({"error": "unauthorized"}), 401

    @app.errorhandler(404)
    def _err_404(_):
        return jsonify({"error": "not found"}), 404

    @app.errorhandler(405)
    def _err_405(_):
        return jsonify({"error": "method not allowed"}), 405

    @app.errorhandler(500)
    def _err_500(_):
        return jsonify({"error": "internal server error"}), 500

    # Create tables on first boot. Idempotent — no-op if tables already exist.
    # Without this, the Postgres DB starts empty and every entity query 500s.
    with app.app_context():
        db.create_all()

    return app
"""),
    "app/models/user.py": string.Template("""\
import uuid

from werkzeug.security import generate_password_hash, check_password_hash

from app.database import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {"id": self.id, "email": self.email,
                "created_at": self.created_at.isoformat() if self.created_at else None}
"""),
    "app/blueprints/auth_bp.py": string.Template("""\
from flask import Blueprint, jsonify, request
from flask_jwt_extended import (
    create_access_token, get_jwt_identity, jwt_required,
    set_access_cookies, unset_jwt_cookies,
)

from app.database import db
from app.models.user import User


auth_bp = Blueprint("auth", __name__)


@auth_bp.post("/register")
def register():
    payload = request.get_json() or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400
    if len(password) < 8:
        return jsonify({"error": "password must be >= 8 chars"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "email already registered"}), 409
    user = User(email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    token = create_access_token(identity=user.id)
    resp = jsonify({"user": user.to_dict()})
    set_access_cookies(resp, token)
    return resp, 201


@auth_bp.post("/login")
def login():
    payload = request.get_json() or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    user = User.query.filter_by(email=email).first()
    if user is None or not user.check_password(password):
        return jsonify({"error": "invalid credentials"}), 401
    token = create_access_token(identity=user.id)
    resp = jsonify({"user": user.to_dict()})
    set_access_cookies(resp, token)
    return resp, 200


@auth_bp.post("/logout")
def logout():
    resp = jsonify({"ok": True})
    unset_jwt_cookies(resp)
    return resp, 200


@auth_bp.get("/me")
@jwt_required()
def me():
    user = User.query.get(get_jwt_identity())
    if user is None:
        return jsonify({"error": "user not found"}), 404
    return jsonify(user.to_dict())
"""),
    "app/database.py": string.Template("""\
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
"""),
    "app/models/__init__.py": string.Template(""),
    "app/models/_entity.py": string.Template("""\
from app.database import db


class $entity_name(db.Model):
    __tablename__ = "$table_name"

    id = db.Column(db.String(36), primary_key=True)
    # Per-tenant ownership. Every row is scoped to the user that created
    # it; queries in the blueprint filter by this. Without it,
    # Project.query.all() would leak every user's data to every other user.
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
$columns
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
$dict_fields
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
"""),
    "app/blueprints/__init__.py": string.Template(""),
    "app/blueprints/_entity_bp.py": string.Template("""\
import uuid

from flask import Blueprint, abort, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.inspection import inspect as _sa_inspect

from app.database import db
from app.models.$snake_entity import $entity_name

${snake_entity}_bp = Blueprint("$snake_entity", __name__)


def _writable_fields():
    # Column names a client is allowed to set on POST/PUT.
    # Excludes server-managed columns. Filtering here is what prevents
    # 'unknown column' 500s when a client sends extra fields, AND it
    # prevents a client from overwriting user_id to spoof ownership.
    server_managed = {"id", "user_id", "created_at", "updated_at"}
    return {c.key for c in _sa_inspect($entity_name).mapper.column_attrs
            if c.key not in server_managed}


def _scoped_or_404(item_id):
    # Per-tenant fetch: returns the row only if it belongs to the caller.
    # Returning 404 (not 403) on a row owned by someone else is intentional —
    # it doesn't reveal that the id exists.
    item = $entity_name.query.filter_by(
        id=item_id, user_id=get_jwt_identity()
    ).first()
    if item is None:
        abort(404)
    return item



@${snake_entity}_bp.get("/")
@jwt_required()
def list_$snake_entity():
    items = $entity_name.query.filter_by(user_id=get_jwt_identity()).all()
    return jsonify([i.to_dict() for i in items])


@${snake_entity}_bp.post("/")
@jwt_required()
def create_$snake_entity():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    allowed = _writable_fields()
    clean = {k: v for k, v in payload.items() if k in allowed}
    item = $entity_name(id=str(uuid.uuid4()), user_id=get_jwt_identity(), **clean)
    db.session.add(item)
    db.session.commit()
    return jsonify(item.to_dict()), 201


@${snake_entity}_bp.get("/<item_id>")
@jwt_required()
def get_$snake_entity(item_id):
    return jsonify(_scoped_or_404(item_id).to_dict())


@${snake_entity}_bp.put("/<item_id>")
@jwt_required()
def update_$snake_entity(item_id):
    item = _scoped_or_404(item_id)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    allowed = _writable_fields()
    for k, v in payload.items():
        if k in allowed:
            setattr(item, k, v)
    db.session.commit()
    return jsonify(item.to_dict())


@${snake_entity}_bp.delete("/<item_id>")
@jwt_required()
def delete_$snake_entity(item_id):
    item = _scoped_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    return "", 204
"""),
    ".env.example": string.Template("""\
DATABASE_URL=postgresql://archiet:archiet@localhost:5432/$bundle_id
SECRET_KEY=$secret_key
JWT_SECRET_KEY=$jwt_secret_key
FRONTEND_URL=http://localhost:3000
FLASK_ENV=production
"""),
    "Dockerfile": string.Template("""\
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["gunicorn", "-b", "0.0.0.0:5000", "wsgi:app"]
"""),
    "docker-compose.yml": string.Template("""\
services:
  app:
    build: .
    ports: ["5000:5000"]
    environment:
      DATABASE_URL: postgresql://archiet:archiet@db:5432/$bundle_id
      SECRET_KEY: $secret_key
      JWT_SECRET_KEY: $jwt_secret_key
      FRONTEND_URL: $${FRONTEND_URL:-http://localhost:3000}
    depends_on:
      db:
        condition: service_healthy
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: archiet
      POSTGRES_PASSWORD: archiet
      POSTGRES_DB: $bundle_id
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U archiet -d $bundle_id"]
      interval: 3s
      timeout: 3s
      retries: 20
volumes:
  pgdata:
"""),
    "README.md": string.Template("""\
# $solution_name

Generated by microcodegen.py: a deterministic Flask + Postgres CRUD app.

## Quick start

```bash
cp .env.example .env
docker compose up
curl http://localhost:5000/api/health
```

## End-to-end (register, login, CRUD)

```bash
# 1. Register. Saves the JWT cookie to ./cookies.txt.
curl -c cookies.txt -X POST http://localhost:5000/api/auth/register \\
     -H "Content-Type: application/json" \\
     -d '{"email":"you@example.com","password":"hunter22hunter"}'

# 2. Create.
curl -b cookies.txt -X POST http://localhost:5000/api/projects/ \\
     -H "Content-Type: application/json" \\
     -d '{"name":"My first project"}'

# 3. List.
curl -b cookies.txt http://localhost:5000/api/projects/
```

Same pattern for the other entities (PUT and DELETE also supported).

## Entities

$entity_list

## Production hardening

Set these in your deploy env before going live:

```bash
FLASK_ENV=production               # enables JWT_COOKIE_SECURE
JWT_COOKIE_CSRF_PROTECT=true       # require X-CSRF-TOKEN on state changes
FRONTEND_URL=https://your.app      # CORS allowlist
SECRET_KEY=<32+ random chars>      # the .env.example value is fine
JWT_SECRET_KEY=<32+ random chars>
```

The app refuses to start if SECRET_KEY or JWT_SECRET_KEY is missing
or still set to a `change-me` placeholder.

## What's included

- Flask + Postgres (docker-compose with healthcheck-gated startup)
- JWT-cookie auth: register / login / logout / me
- Full CRUD per entity (list, create, get, update, delete)
- JSON error handlers, CORS scoped to FRONTEND_URL, per-ZIP random secrets

## What's NOT included

No frontend, no email/SMS, no payment integration, no rate limiting,
no audit logging, no Alembic migrations (uses `db.create_all` on first
boot). Add as needed.
"""),
    "app/static/index.html": string.Template("""\
<!doctype html>
<meta charset="utf-8">
<title>$solution_name</title>
<style>
  body{font:14px system-ui,sans-serif;max-width:780px;margin:40px auto;padding:0 16px;color:#222}
  h1{margin:0 0 4px}h2{margin:20px 0 8px;font-size:16px}
  form{display:flex;gap:8px;margin:8px 0;flex-wrap:wrap}
  input,button,select{font:inherit;padding:6px 10px;border:1px solid #ccc;border-radius:4px}
  button{background:#222;color:#fff;border-color:#222;cursor:pointer}
  button.ghost{background:#f4f4f4;color:#222;border-color:#ddd}
  button.danger{background:#b00;border-color:#b00}
  .row{display:flex;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid #eee}
  .row .name{flex:1}
  .hidden{display:none}
  .err{color:#b00;margin:8px 0;min-height:20px}
  .tabs{display:flex;gap:4px;border-bottom:2px solid #eee;margin:16px 0}
  .tab{padding:8px 16px;border:none;background:transparent;color:#666;border-radius:0;cursor:pointer;font-weight:600}
  .tab.active{color:#222;border-bottom:2px solid #222;margin-bottom:-2px}
  code{background:#f4f4f4;padding:1px 4px;border-radius:3px}
</style>
<h1>$solution_name</h1>
<p>Generated by microcodegen. Register, then CRUD any entity below.</p>

<div id="auth">
  <h2>Sign in / Register</h2>
  <form id="auth-form">
    <input id="email" type="email" placeholder="you@example.com" required>
    <input id="password" type="password" placeholder="password (8+ chars)" minlength="8" required>
    <button type="submit" data-action="login">Login</button>
    <button type="submit" data-action="register" class="ghost">Register</button>
  </form>
</div>

<div id="app" class="hidden">
  <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
    <span>Signed in as <strong id="who"></strong></span>
    <button id="logout" class="ghost">Logout</button>
  </div>
  <div id="tabs" class="tabs"></div>
  <h2 id="entity-heading"></h2>
  <form id="create-form">
    <input id="new-name" placeholder="new name" required>
    <button>Add</button>
  </form>
  <div id="list"></div>
</div>

<p class="err" id="err"></p>

<script>
const ENTITIES = $entities_json;
let active = ENTITIES[0];
let action = "login";
const $$ = id => document.getElementById(id);
function err(m){ $$("err").textContent = m || ""; }
async function api(method, path, body){
  const r = await fetch(path, {
    method, credentials: "include",
    headers: body ? {"Content-Type":"application/json"} : {},
    body: body ? JSON.stringify(body) : null,
  });
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) throw new Error(data?.error || r.statusText);
  return data;
}
function renderTabs(){
  $$("tabs").innerHTML = "";
  ENTITIES.forEach(e => {
    const b = document.createElement("button");
    b.className = "tab" + (e.url === active.url ? " active" : "");
    b.textContent = e.plural;
    b.onclick = () => { active = e; renderTabs(); refresh(); };
    $$("tabs").append(b);
  });
  $$("entity-heading").textContent = active.plural;
  $$("new-name").placeholder = "new " + active.singular + " name";
}
async function refresh(){
  try {
    const me = await api("GET", "/api/auth/me");
    $$("auth").classList.add("hidden");
    $$("app").classList.remove("hidden");
    $$("who").textContent = me.email;
    renderTabs();
    const items = await api("GET", `/api/${active.url}/`);
    $$("list").innerHTML = "";
    items.forEach(it => {
      const row = document.createElement("div"); row.className = "row";
      const name = document.createElement("input"); name.value = it.name || ""; name.className = "name";
      const save = document.createElement("button"); save.textContent = "Save"; save.className = "ghost";
      const del = document.createElement("button"); del.textContent = "Delete"; del.className = "danger";
      save.onclick = async () => { try { await api("PUT", `/api/${active.url}/${it.id}`, {name: name.value}); err(""); } catch(e){ err(e.message); } };
      del.onclick = async () => { try { await api("DELETE", `/api/${active.url}/${it.id}`); refresh(); } catch(e){ err(e.message); } };
      row.append(name, save, del);
      $$("list").append(row);
    });
  } catch(e) {
    $$("auth").classList.remove("hidden");
    $$("app").classList.add("hidden");
  }
}
document.querySelectorAll("#auth-form button").forEach(b => b.addEventListener("click", e => { action = e.target.dataset.action; }));
$$("auth-form").addEventListener("submit", async e => {
  e.preventDefault();
  err("");
  try {
    await api("POST", "/api/auth/" + action, {email: $$("email").value, password: $$("password").value});
    refresh();
  } catch(ex) { err(ex.message); }
});
$$("logout").addEventListener("click", async () => { try { await api("POST", "/api/auth/logout"); refresh(); } catch(e){ err(e.message); } });
$$("create-form").addEventListener("submit", async e => {
  e.preventDefault();
  try { await api("POST", `/api/${active.url}/`, {name: $$("new-name").value}); $$("new-name").value = ""; refresh(); }
  catch(ex) { err(ex.message); }
});
refresh();
</script>
"""),
    "tests/test_health.py": string.Template("""\
def test_health(app_client):
    r = app_client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
"""),
    "tests/conftest.py": string.Template("""\
import os

# Set test secrets BEFORE importing the app — Config.SECRET_KEY/JWT_SECRET_KEY
# raise at import time if env vars are missing (a deliberate prod safety),
# so tests must supply them up-front.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-not-for-production-use")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest  # noqa: E402

from app import create_app  # noqa: E402
from app.database import db  # noqa: E402


@pytest.fixture
def app_client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.drop_all()
"""),
}


def _column_for_field(fname: str, fspec: dict) -> str:
    """SQLAlchemy column declaration line for a single field."""
    type_map = {
        "string": "db.String(255)",
        "text": "db.Text",
        "integer": "db.Integer",
        "int": "db.Integer",
        "float": "db.Float",
        "decimal": "db.Numeric(12, 2)",
        "boolean": "db.Boolean",
        "bool": "db.Boolean",
        "datetime": "db.DateTime",
        "date": "db.Date",
        "uuid": "db.String(36)",
        "json": "db.JSON",
    }
    sa_type = type_map.get(fspec["type"], "db.String(255)")
    nullable = "" if fspec.get("required") else ", nullable=True"
    unique = ", unique=True" if fspec.get("unique") else ""
    indexed = ", index=True" if fspec.get("indexed") else ""
    return f"    {fname} = db.Column({sa_type}{nullable}{unique}{indexed})"


def render_genome(genome: dict) -> dict[str, str]:
    """Render the genome into a {path: content} dict.

    The output is what the customer would download. Flask only.
    """
    bundle_id = genome["bundle_id"]
    name = genome["solution_name"]
    # Per-ZIP random secrets. Each generated ZIP gets unique JWT/SECRET keys
    # so two customers downloading the same blueprint cannot forge tokens
    # against each other's deployments. The customer can override via env.
    secret_key = secrets.token_urlsafe(32)
    jwt_secret_key = secrets.token_urlsafe(32)
    files: dict[str, str] = {}

    # Top-level files (no per-entity substitution)
    for path in (
        "requirements.txt",
        "wsgi.py",
        "config.py",
        "app/database.py",
        "app/__init__.py",
        ".env.example",
        "Dockerfile",
        "docker-compose.yml",
        "tests/test_health.py",
        "tests/conftest.py",
    ):
        # __init__.py is rendered separately because it needs blueprint wiring.
        if path == "app/__init__.py":
            continue
        files[path] = _FILE_TEMPLATES[path].safe_substitute(
            bundle_id=bundle_id,
            secret_key=secret_key,
            jwt_secret_key=jwt_secret_key,
        )

    # Empty package markers
    files["app/models/__init__.py"] = ""
    files["app/blueprints/__init__.py"] = ""

    # Auth scaffold (always emitted — every entity blueprint is @jwt_required
    # so a customer cannot use the CRUD until they can register/login).
    files["app/models/user.py"] = _FILE_TEMPLATES[
        "app/models/user.py"
    ].safe_substitute()
    files["app/blueprints/auth_bp.py"] = _FILE_TEMPLATES[
        "app/blueprints/auth_bp.py"
    ].safe_substitute()

    # Per-entity files
    blueprint_imports: list[str] = []
    blueprint_registrations: list[str] = []
    entity_list_lines: list[str] = []

    entities = (genome["modules"]["core"] or {}).get("entities") or {}
    for ent_name, ent_spec in entities.items():
        snake = _snake(ent_name)
        table = snake + "s"

        # Build the column declarations for the model.
        cols: list[str] = []
        dict_fields: list[str] = []
        for fname, fspec in (ent_spec.get("fields") or {}).items():
            if fname == "id":
                continue
            cols.append(_column_for_field(fname, fspec))
            dict_fields.append(f'            "{fname}": self.{fname},')

        files[f"app/models/{snake}.py"] = _FILE_TEMPLATES[
            "app/models/_entity.py"
        ].safe_substitute(
            entity_name=ent_name,
            table_name=table,
            columns="\n".join(cols) if cols else "    pass",
            dict_fields="\n".join(dict_fields),
        )
        files[f"app/blueprints/{snake}_bp.py"] = _FILE_TEMPLATES[
            "app/blueprints/_entity_bp.py"
        ].safe_substitute(
            entity_name=ent_name,
            snake_entity=snake,
        )

        blueprint_imports.append(
            f"    from app.blueprints.{snake}_bp import {snake}_bp"
        )
        blueprint_registrations.append(
            f'    app.register_blueprint({snake}_bp, url_prefix="/api/{snake}s")'
        )
        entity_list_lines.append(f"- **{ent_name}**: {ent_spec.get('description', '')}")

    files["app/__init__.py"] = _FILE_TEMPLATES["app/__init__.py"].safe_substitute(
        bundle_id=bundle_id,
        blueprint_imports="\n".join(blueprint_imports)
        if blueprint_imports
        else "    pass",
        blueprint_registrations="\n".join(blueprint_registrations),
    )

    # Single-page browser demo. Renders a tab per entity so the customer
    # can CRUD any of them, not just the first. The JS array below feeds
    # the tab strip + active-entity URL routing.
    if entities:
        entity_list_for_ui = [
            {
                "url": _snake(ent) + "s",
                "singular": ent.lower(),
                "plural": ent + "s",
            }
            for ent in entities.keys()
        ]
    else:
        entity_list_for_ui = [{"url": "items", "singular": "item", "plural": "Items"}]
    files["app/static/index.html"] = _FILE_TEMPLATES[
        "app/static/index.html"
    ].safe_substitute(
        solution_name=name,
        entities_json=json.dumps(entity_list_for_ui),
    )

    files["README.md"] = _FILE_TEMPLATES["README.md"].safe_substitute(
        solution_name=name,
        entity_list="\n".join(entity_list_lines)
        or "_(no entities extracted from PRD)_",
    )

    # Genome itself shipped for transparency — customer can read what we
    # extracted and regen with edits if the heuristics missed something.
    files["GENOME.json"] = json.dumps(genome, indent=2, default=str)

    return files


# ─── STAGE 4 ────────────────────────────────────────────────────────────────
# pack(files) → bytes. stdlib zipfile.


def pack(files: dict[str, str]) -> bytes:
    """Pack {path: content} into ZIP bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in sorted(files.items()):
            zf.writestr(path, content)
    return buf.getvalue()


# ─── PUBLIC ENTRY ───────────────────────────────────────────────────────────


def microcodegen(prd_text: str) -> bytes:
    """The complete algorithm. PRD text → ZIP bytes."""
    manifest = parse_prd(prd_text)
    genome = manifest_to_genome(manifest)
    files = render_genome(genome)
    return pack(files)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Archiet's core algorithm in one file.")
    p.add_argument("prd", help="Path to PRD file (markdown/text).")
    p.add_argument(
        "--out",
        help="Directory to extract into. If omitted, writes ZIP bytes to stdout.",
    )
    args = p.parse_args(argv)

    prd_text = Path(args.prd).read_text(encoding="utf-8")

    if args.out:
        manifest = parse_prd(prd_text)
        genome = manifest_to_genome(manifest)
        files = render_genome(genome)
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        for path, content in files.items():
            full = out_dir / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
        print(f"Wrote {len(files)} files to {out_dir}", file=sys.stderr)
    else:
        zip_bytes = microcodegen(prd_text)
        sys.stdout.buffer.write(zip_bytes)

    return 0


if __name__ == "__main__":
    sys.exit(main())
