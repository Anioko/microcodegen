#!/usr/bin/env python3
"""archiet_microcodegen_flask._core — Archiet's Flask algorithm in one file.

PRD text → manifest → genome → rendered Flask app → ZIP bytes.

Contract:  microcodegen_flask(prd_text) → bytes  (working, bootable Flask ZIP)

  # CLI:  python -m archiet_microcodegen_flask prd.md > app.zip
  #       python -m archiet_microcodegen_flask prd.md --out /tmp/myapp/
  # Lib:  from archiet_microcodegen_flask import microcodegen_flask

Stages:
  1. parse_prd(text) → manifest dict   (regex extraction, no LLM)
  2. manifest_to_genome(manifest) → genome dict
  3. render_genome(genome) → {path: content}   (string.Template, Flask only)
  4. pack(files) → bytes   (stdlib zipfile)

NOT: LLM extraction, multi-stack, capability emission, frontend, stub-fill,
     quality scoring, rate limiting, observability, secret rotation.

Constraints:
  - Pure stdlib; zero app.* / agents.* / templates/ imports.
  - No TODOs — handle it or scope it out explicitly.
  - Hard ceiling: 1400 LOC.
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
# Pure regex + heuristic extraction. Copied verbatim from microcodegen.py.
# Language-agnostic: no FastAPI or Flask concepts here.

_ENTITY_PATTERN = re.compile(
    r"^#{1,3}\s*(?:entities|data models|domain models|entity list)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_ENTITY_NAME_PATTERN = re.compile(
    r"^[\s\-\*\#]+\*{0,2}([A-Z][a-zA-Z0-9_]{1,40})\*{0,2}[ \t]*(?::|—|-|[ \t]|$)",
    re.MULTILINE,
)

_FIELD_PATTERN = re.compile(
    r"^[\s\-\*]+([a-z_][a-z0-9_]{0,40})\s*[:—-]\s*([a-zA-Z]+)([^\n]*)",
    re.MULTILINE,
)

_INLINE_FIELD_PATTERN = re.compile(
    r"([a-z_][a-z0-9_]{0,40})\s*\(\s*([a-zA-Z]+)([^)]*)\)",
)

_USER_STORY_PATTERN = re.compile(
    r"As\s+(?:a|an)\s+([^,]+?),\s+I\s+want\s+(?:to\s+)?([^,]+?)(?:,?\s*so\s+that\s+([^.]+))?\.",
    re.IGNORECASE,
)

_INTEGRATION_KEYWORDS = {
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
    """Extract 'required', 'unique', 'indexed' flags from modifier text."""
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

    entities: list[dict] = []
    section_match = _ENTITY_PATTERN.search(text)
    entity_section = ""
    if section_match:
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
        ent_start = m.end()
        next_entity = _ENTITY_NAME_PATTERN.search(entity_section, ent_start)
        ent_end = next_entity.start() if next_entity else len(entity_section)
        ent_body = entity_section[ent_start:ent_end]

        fields: list[dict] = []
        seen_fields: set[str] = set()
        for fm in _FIELD_PATTERN.finditer(ent_body):
            fname, ftype, modifier_text = fm.group(1), fm.group(2), fm.group(3)
            if fname in seen:
                continue
            if fname in seen_fields:
                continue
            seen_fields.add(fname)
            flags = _parse_field_modifiers(modifier_text)
            fields.append({"name": fname, "type": ftype.lower(), **flags})

        if not fields:
            entity_name_line = (
                entity_section[m.start():m.end()] + ent_body.split("\n", 1)[0]
            )
            for im in _INLINE_FIELD_PATTERN.finditer(entity_name_line):
                fname, ftype, modifier_text = im.group(1), im.group(2), im.group(3)
                if fname in seen_fields:
                    continue
                seen_fields.add(fname)
                flags = _parse_field_modifiers(modifier_text)
                fields.append({"name": fname, "type": ftype.lower(), **flags})

        entities.append({"name": ename, "fields": fields})

    stories = []
    for m in _USER_STORY_PATTERN.finditer(text):
        stories.append({
            "as_a": (m.group(1) or "").strip(),
            "i_want": (m.group(2) or "").strip(),
            "so_that": (m.group(3) or "").strip(),
        })

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
# Maps the heuristic manifest into the canonical genome shape.
# Language-agnostic: copied verbatim from microcodegen.py with
# language field changed to "flask".


def _snake(s: str) -> str:
    """Convert "Order Line" → "order_line"."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.strip()).strip("_")
    s = re.sub(r"([a-z])([A-Z])", r"\1_\2", s)
    return s.lower()


def manifest_to_genome(manifest: dict) -> dict:
    """Map the heuristic manifest into the canonical genome shape.

    Identical to the FastAPI version except language="flask".
    """
    name = manifest["solution_name"]
    snake_name = _snake(name)

    entities_dict: dict[str, dict] = {}
    for ent in manifest.get("entities", []):
        fields: dict[str, dict] = {"id": {"type": "uuid", "required": True}}
        for f in ent.get("fields", []):
            if f["name"] in ("id", "created_at", "updated_at"):
                continue
            fields[f["name"]] = {
                "type": f["type"],
                "required": f["required"],
                "unique": f["unique"],
                "indexed": f["indexed"],
            }
        entities_dict[ent["name"]] = {
            "fields": fields,
            "description": f"{ent['name']} entity (generated by microcodegen-flask)",
            "archimate_type": "DataObject",
        }

    _workflow_verbs = {
        "create", "update", "delete", "approve", "reject", "submit",
        "complete", "process", "generate", "schedule", "notify",
    }
    archimate_elements: list[dict] = [
        {
            "name": name,
            "type": "ApplicationComponent",
            "description": f"{name} Flask application",
        },
    ]
    for ent_name in entities_dict:
        archimate_elements.append({
            "name": ent_name,
            "type": "DataObject",
            "description": entities_dict[ent_name]["description"],
        })
    for story in manifest.get("user_stories", []):
        text = story.get("i_want", story.get("story", "")).lower()
        if any(v in text for v in _workflow_verbs):
            label = text[:60]
            archimate_elements.append({
                "name": label,
                "type": "BusinessProcess",
                "description": f"I want to {text}",
            })
    for intg in manifest.get("integrations", []):
        intg_name = intg.get("name", str(intg)) if isinstance(intg, dict) else str(intg)
        archimate_elements.append({
            "name": intg_name,
            "type": "ApplicationService",
            "description": f"External integration: {intg_name}",
        })

    return {
        "genome_version": "1.0.0",
        "solution_id": 0,
        "solution_name": name,
        "bundle_id": snake_name,
        "language": "flask",
        "modules": {
            "core": {
                "module_type": "crud",
                "description": "Core entities",
                "entities": entities_dict,
            },
        },
        "user_stories": manifest.get("user_stories", []),
        "integrations": manifest.get("integrations", []),
        "archimate_elements": archimate_elements,
    }


# ─── STAGE 3 ────────────────────────────────────────────────────────────────
# render_genome(genome) → {path: content}.
#
# Flask + SQLAlchemy 2.0 + Flask-JWT-Extended + Flask-Migrate.
# httpOnly JWT cookies. Blueprint-per-entity CRUD.

_FLASK_TEMPLATES: dict[str, string.Template] = {
    "requirements.txt": string.Template("""\
flask>=3.0
flask-sqlalchemy>=3.1
flask-jwt-extended>=4.6
flask-migrate>=4.0
flask-cors>=4.0
psycopg2-binary>=2.9
python-dotenv>=1.0
"""),

    "config.py": string.Template("""\
import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    if not SQLALCHEMY_DATABASE_URI:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # JWT stored in httpOnly cookies — never localStorage, never response body.
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
    if not JWT_SECRET_KEY:
        raise RuntimeError("JWT_SECRET_KEY environment variable is not set.")
    JWT_TOKEN_LOCATION = ["cookies"]
    JWT_COOKIE_HTTPONLY = True
    JWT_COOKIE_SAMESITE = "Lax"
    JWT_ACCESS_TOKEN_EXPIRES_MINUTES = 60 * 24 * 7  # 7 days
"""),

    "app/__init__.py": string.Template("""\
from flask import Flask

from app.extensions import db, jwt, migrate


def create_app(config_object="config.Config"):
    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)
    jwt.init_app(app)
    migrate.init_app(app, db)

    # Import models so Flask-Migrate discovers them.
    with app.app_context():
        import app.models  # noqa: F401

    from app.auth.routes import auth_bp
    app.register_blueprint(auth_bp, url_prefix="/api/auth")

$blueprint_registrations

    @app.get("/health")
    def health():
        from flask import jsonify
        return jsonify({"status": "ok", "version": "$bundle_id"})

    return app
"""),

    "app/extensions.py": string.Template("""\
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
jwt = JWTManager()
migrate = Migrate()
"""),

    "app/models/__init__.py": string.Template("""\
# Import all models here so Flask-Migrate / db.create_all() discovers them.
from app.models.user import User  # noqa: F401
$model_imports
"""),

    "app/models/user.py": string.Template("""\
from datetime import datetime, timezone

from app.extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
"""),

    "app/models/_entity.py": string.Template("""\
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Float
from sqlalchemy import Integer, Numeric, String, Text
try:
    from sqlalchemy import JSON
except ImportError:
    from sqlalchemy import JSON  # SQLAlchemy 2.0+

from app.extensions import db


class $entity_name(db.Model):
    __tablename__ = "$table_name"

    id = db.Column(db.String(36), primary_key=True)
    # Per-tenant ownership: every row is scoped to the user that created it.
    # All queries in the blueprint filter by this to prevent cross-user leaks.
    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
$columns
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
"""),

    "app/auth/__init__.py": string.Template(""),

    "app/auth/routes.py": string.Template("""\
import uuid
from datetime import timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    get_jwt_identity,
    jwt_required,
    set_access_cookies,
    unset_jwt_cookies,
)
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.user import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered"}), 409
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=generate_password_hash(password),
    )
    db.session.add(user)
    db.session.commit()
    token = create_access_token(
        identity=user.id, expires_delta=timedelta(days=7)
    )
    resp = jsonify({"id": user.id, "email": user.email})
    resp.status_code = 201
    set_access_cookies(resp, token)
    return resp


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "Invalid credentials"}), 401
    token = create_access_token(
        identity=user.id, expires_delta=timedelta(days=7)
    )
    resp = jsonify({"id": user.id, "email": user.email})
    set_access_cookies(resp, token)
    return resp


@auth_bp.post("/logout")
def logout():
    resp = jsonify({"ok": True})
    unset_jwt_cookies(resp)
    return resp


@auth_bp.get("/me")
@jwt_required()
def me():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"id": user.id, "email": user.email})
"""),

    "app/routes/_entity.py": string.Template("""\
import uuid

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.models.$snake_entity import $entity_name

${snake_entity}_bp = Blueprint("${snake_entity}", __name__)


@${snake_entity}_bp.get("/")
@jwt_required()
def list_${snake_entity}s():
    user_id = get_jwt_identity()
    items = $entity_name.query.filter_by(user_id=user_id).all()
    return jsonify([_to_dict(i) for i in items])


@${snake_entity}_bp.post("/")
@jwt_required()
def create_${snake_entity}():
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    obj = $entity_name(id=str(uuid.uuid4()), user_id=user_id)
$field_setters
    db.session.add(obj)
    db.session.commit()
    return jsonify(_to_dict(obj)), 201


@${snake_entity}_bp.get("/<string:item_id>")
@jwt_required()
def get_${snake_entity}(item_id):
    user_id = get_jwt_identity()
    obj = $entity_name.query.filter_by(id=item_id, user_id=user_id).first()
    if not obj:
        return jsonify({"error": "$entity_name not found"}), 404
    return jsonify(_to_dict(obj))


@${snake_entity}_bp.put("/<string:item_id>")
@jwt_required()
def update_${snake_entity}(item_id):
    user_id = get_jwt_identity()
    obj = $entity_name.query.filter_by(id=item_id, user_id=user_id).first()
    if not obj:
        return jsonify({"error": "$entity_name not found"}), 404
    data = request.get_json(silent=True) or {}
$field_updaters
    db.session.commit()
    return jsonify(_to_dict(obj))


@${snake_entity}_bp.delete("/<string:item_id>")
@jwt_required()
def delete_${snake_entity}(item_id):
    user_id = get_jwt_identity()
    obj = $entity_name.query.filter_by(id=item_id, user_id=user_id).first()
    if not obj:
        return jsonify({"error": "$entity_name not found"}), 404
    db.session.delete(obj)
    db.session.commit()
    return "", 204


def _to_dict(obj) -> dict:
    d = {
        "id": obj.id,
        "user_id": obj.user_id,
$dict_fields
    }
    if hasattr(obj, "created_at") and obj.created_at:
        d["created_at"] = obj.created_at.isoformat()
    if hasattr(obj, "updated_at") and obj.updated_at:
        d["updated_at"] = obj.updated_at.isoformat()
    return d
"""),

    "manage.py": string.Template("""\
import os

from flask.cli import FlaskGroup

from app import create_app

app = create_app()
cli = FlaskGroup(app)

if __name__ == "__main__":
    cli()
"""),

    ".env.example": string.Template("""\
DATABASE_URL=postgresql://archiet:archiet@localhost:5432/$bundle_id
JWT_SECRET_KEY=$jwt_secret_key
SECRET_KEY=$secret_key
"""),

    "Dockerfile": string.Template("""\
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
ENV FLASK_APP=manage.py
CMD ["flask", "--app", "manage:app", "run", "--host", "0.0.0.0", "--port", "5000"]
"""),

    "docker-compose.yml": string.Template("""\
services:
  app:
    build: .
    ports: ["5000:5000"]
    environment:
      DATABASE_URL: postgresql://archiet:archiet@db:5432/$bundle_id
      JWT_SECRET_KEY: $jwt_secret_key
      SECRET_KEY: $secret_key
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

    "README_app.md": string.Template("""\
# $solution_name

Generated by archiet-microcodegen-flask — Flask + SQLAlchemy + PostgreSQL.

## Quick start

```bash
cp .env.example .env
docker compose up
curl http://localhost:5000/health
```

## End-to-end (register, login, CRUD)

```bash
# 1. Register (JWT set as httpOnly cookie)
curl -c cookies.txt -X POST http://localhost:5000/api/auth/register \\
     -H "Content-Type: application/json" \\
     -d '{"email":"you@example.com","password":"hunter22hunter"}'

# 2. Create a record
curl -b cookies.txt -X POST http://localhost:5000/api/items/ \\
     -H "Content-Type: application/json" \\
     -d '{"name":"My first item"}'

# 3. List
curl -b cookies.txt http://localhost:5000/api/items/
```

## Entities

$entity_list

## Migrations

```bash
flask --app manage:app db init
flask --app manage:app db migrate -m "init"
flask --app manage:app db upgrade
```

## What's included

- Flask 3 + PostgreSQL (docker-compose with healthcheck-gated startup)
- JWT-cookie auth (httpOnly, SameSite=Lax): register / login / logout / me
- Flask-JWT-Extended with JWT_TOKEN_LOCATION=["cookies"]
- SQLAlchemy 2.0 models with per-tenant user_id FK on every entity
- Full CRUD per entity: list, create, get, update, delete
- Per-tenant data isolation — every row scoped to the authenticated user
- Flask-Migrate pre-configured
- ARCHITECTURE.md + openapi.yaml shipped in this ZIP
"""),

    "tests/test_app.py": string.Template("""\
\"\"\"Smoke tests for the generated Flask app.

Run:  pytest tests/test_app.py -v

Requires PostgreSQL reachable at DATABASE_URL (or TEST_DATABASE_URL).
Tests are skipped automatically if Postgres is not available.
\"\"\"
import os
import uuid

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-production")
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL", "postgresql://archiet:archiet@localhost:5432/${bundle_id}_test"),
)
os.environ.setdefault("SECRET_KEY", "test-flask-secret")


def _db_reachable() -> bool:
    try:
        import sqlalchemy
        engine = sqlalchemy.create_engine(os.environ["DATABASE_URL"])
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def require_db():
    if not _db_reachable():
        pytest.skip(
            "PostgreSQL not reachable — start with `docker compose up -d db` "
            "or set TEST_DATABASE_URL and retry."
        )


@pytest.fixture
def client():
    from app import create_app
    from app.extensions import db as _db

    app = create_app()
    app.config["TESTING"] = True
    app.config["JWT_COOKIE_CSRF_PROTECT"] = False

    with app.app_context():
        _db.create_all()
        with app.test_client() as c:
            yield c
        _db.drop_all()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"


def test_register_and_login(client):
    email = f"test-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "hunter22hunter"})
    assert r.status_code == 201, r.data
    data = r.get_json()
    assert "id" in data
    assert data["email"] == email

    # Login with same credentials
    r2 = client.post("/api/auth/login", json={"email": email, "password": "hunter22hunter"})
    assert r2.status_code == 200, r2.data

    # Logout
    r3 = client.post("/api/auth/logout")
    assert r3.status_code == 200


def test_me_requires_auth(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401
"""),
}


def _column_for_field(fname: str, fspec: dict) -> str:
    """SQLAlchemy Column() declaration for a single entity field."""
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
    sa_type = type_map.get(fspec.get("type", "string"), "db.String(255)")
    nullable = "" if fspec.get("required") else ", nullable=True"
    unique = ", unique=True" if fspec.get("unique") else ""
    indexed = ", index=True" if fspec.get("indexed") else ""
    return f"    {fname} = db.Column({sa_type}{nullable}{unique}{indexed})"


def _render_architecture_md(genome: dict, entities: dict) -> str:
    """Generate ARCHITECTURE.md with ArchiMate 3.2 element notation."""
    name = genome["solution_name"]
    elements = genome.get("archimate_elements", [])
    user_stories = genome.get("user_stories", [])
    integrations = genome.get("integrations", [])

    lines: list[str] = [
        f"# Architecture — {name}",
        "",
        "Generated by archiet-microcodegen-flask · ArchiMate 3.2 element notation",
        "",
        "## Application Layer (ArchiMate §9)",
        "",
        "| Element | Type | Description |",
        "|---------|------|-------------|",
    ]
    for el in elements:
        lines.append(f"| `{el['name']}` | {el['type']} | {el['description']} |")

    lines += [
        "",
        "## Relationships",
        "",
        "```",
        f"  {name} (ApplicationComponent)",
    ]
    for ent_name in entities:
        lines.append(f"    └── {ent_name} (DataObject)  [Realization]")
    for intg in integrations:
        intg_name = intg.get("name", str(intg)) if isinstance(intg, dict) else str(intg)
        lines.append(f"    └── {intg_name} (ApplicationService)  [UsedBy]")
    lines.append("```")

    if user_stories:
        lines += ["", "## Business Process Layer (ArchiMate §8)", ""]
        for story in user_stories[:10]:
            lines.append(
                f"- As a {story.get('as_a', '')}, I want to {story.get('i_want', '')}"
            )

    lines += [
        "",
        "## Stack",
        "",
        "- **Flask 3** — WSGI application factory (`create_app()`)",
        "- **SQLAlchemy 2.0** — ORM models with per-tenant `user_id` FK",
        "- **Flask-JWT-Extended** — httpOnly cookie auth (`JWT_TOKEN_LOCATION=[\"cookies\"]`)",
        "- **Flask-Migrate** — Alembic-based schema migrations",
        "- **PostgreSQL 16** — primary datastore",
        "",
        "## Notes",
        "",
        "- This file is heuristically derived from PRD text.",
        "- The full Archiet platform generates a formal ArchiMate 3.2 model",
        "  (ApplicationComponent, DataObject, BusinessProcess, ApplicationService,",
        "  AssignmentRelationship, RealizationRelationship) from the genome IR,",
        "  plus DMN 1.5 decision tables and BPMN 2.0 process diagrams.",
        "- To regenerate: edit GENOME.json and re-run archiet-microcodegen-flask,",
        "  or use the Archiet platform for cross-stack + formal-model output.",
    ]
    return "\n".join(lines) + "\n"


def _render_openapi_yaml(genome: dict, entities: dict) -> str:
    """Generate openapi.yaml (OpenAPI 3.1) for the Flask app."""
    name = genome["solution_name"]

    _type_map = {
        "string": "string",
        "text": "string",
        "integer": "integer",
        "float": "number",
        "boolean": "boolean",
        "datetime": "string",
        "date": "string",
        "uuid": "string",
        "json": "object",
    }

    lines: list[str] = [
        "openapi: '3.1.0'",
        "info:",
        f"  title: {name} API",
        f"  description: Generated by archiet-microcodegen-flask for {name}",
        "  version: 0.1.0",
        "servers:",
        "  - url: http://localhost:5000",
        "    description: Local development",
        "paths:",
        "  /api/auth/register:",
        "    post:",
        "      summary: Register a new user",
        "      tags: [auth]",
        "      requestBody:",
        "        required: true",
        "        content:",
        "          application/json:",
        "            schema:",
        "              $ref: '#/components/schemas/AuthRequest'",
        "      responses:",
        "        '201': {description: User created}",
        "        '409': {description: Email already registered}",
        "  /api/auth/login:",
        "    post:",
        "      summary: Login and receive JWT cookie",
        "      tags: [auth]",
        "      requestBody:",
        "        required: true",
        "        content:",
        "          application/json:",
        "            schema:",
        "              $ref: '#/components/schemas/AuthRequest'",
        "      responses:",
        "        '200': {description: Authenticated — JWT set in httpOnly cookie}",
        "        '401': {description: Invalid credentials}",
        "  /api/auth/logout:",
        "    post:",
        "      summary: Logout — clears JWT cookie",
        "      tags: [auth]",
        "      responses:",
        "        '200': {description: Logged out}",
        "  /api/auth/me:",
        "    get:",
        "      summary: Get current user",
        "      tags: [auth]",
        "      security: [{cookieAuth: []}]",
        "      responses:",
        "        '200': {description: Current user}",
        "        '401': {description: Not authenticated}",
    ]

    for ent_name, ent_spec in entities.items():
        snake = _snake(ent_name)
        plural = snake + "s"
        props: list[str] = []
        for fname, fspec in (ent_spec.get("fields") or {}).items():
            if fname == "id":
                continue
            oa_type = _type_map.get(fspec.get("type", "string"), "string")
            fmt = ""
            if fspec.get("type") in ("datetime", "date"):
                fmt = f"\n            format: {fspec['type']}-time"
            props.append(f"            {fname}:\n              type: {oa_type}{fmt}")
        lines += [
            f"  /api/{plural}/:",
            "    get:",
            f"      summary: List {ent_name} records",
            f"      tags: [{ent_name}]",
            "      security: [{cookieAuth: []}]",
            "      responses:",
            f"        '200': {{description: List of {ent_name}}}",
            "    post:",
            f"      summary: Create {ent_name}",
            f"      tags: [{ent_name}]",
            "      security: [{cookieAuth: []}]",
            "      requestBody:",
            "        required: true",
            "        content:",
            "          application/json:",
            "            schema:",
            f"              $ref: '#/components/schemas/{ent_name}'",
            "      responses:",
            f"        '201': {{description: {ent_name} created}}",
            f"  /api/{plural}/{{id}}:",
            "    get:",
            f"      summary: Get {ent_name} by ID",
            f"      tags: [{ent_name}]",
            "      security: [{cookieAuth: []}]",
            "      parameters:",
            "        - in: path",
            "          name: id",
            "          required: true",
            "          schema: {type: string, format: uuid}",
            "      responses:",
            f"        '200': {{description: {ent_name} record}}",
            "        '404': {description: Not found}",
            "    put:",
            f"      summary: Update {ent_name}",
            f"      tags: [{ent_name}]",
            "      security: [{cookieAuth: []}]",
            "      parameters:",
            "        - in: path",
            "          name: id",
            "          required: true",
            "          schema: {type: string, format: uuid}",
            "      requestBody:",
            "        required: true",
            "        content:",
            "          application/json:",
            "            schema:",
            f"              $ref: '#/components/schemas/{ent_name}'",
            "      responses:",
            f"        '200': {{description: {ent_name} updated}}",
            "    delete:",
            f"      summary: Delete {ent_name}",
            f"      tags: [{ent_name}]",
            "      security: [{cookieAuth: []}]",
            "      parameters:",
            "        - in: path",
            "          name: id",
            "          required: true",
            "          schema: {type: string, format: uuid}",
            "      responses:",
            "        '204': {description: Deleted}",
        ]

    lines += [
        "components:",
        "  securitySchemes:",
        "    cookieAuth:",
        "      type: apiKey",
        "      in: cookie",
        "      name: access_token_cookie",
        "  schemas:",
        "    AuthRequest:",
        "      type: object",
        "      required: [email, password]",
        "      properties:",
        "        email: {type: string, format: email}",
        "        password: {type: string, format: password}",
    ]
    for ent_name, ent_spec in entities.items():
        lines += [
            f"    {ent_name}:",
            "      type: object",
            "      properties:",
        ]
        for fname, fspec in (ent_spec.get("fields") or {}).items():
            if fname == "id":
                continue
            oa_type = _type_map.get(fspec.get("type", "string"), "string")
            lines.append(f"        {fname}: {{type: {oa_type}}}")

    return "\n".join(lines) + "\n"


def render_genome(genome: dict) -> dict[str, str]:
    """Render the genome into a {path: content} dict.

    Flask + SQLAlchemy + Flask-JWT-Extended + Flask-Migrate.
    JWT stored in httpOnly cookies — never localStorage, never Authorization header.
    """
    bundle_id = genome["bundle_id"]
    name = genome["solution_name"]
    jwt_secret_key = secrets.token_urlsafe(32)
    secret_key = secrets.token_urlsafe(32)
    files: dict[str, str] = {}

    # Fixed files
    for path in ("requirements.txt", "Dockerfile", "docker-compose.yml", "config.py"):
        files[path] = _FLASK_TEMPLATES[path].safe_substitute(
            bundle_id=bundle_id,
            jwt_secret_key=jwt_secret_key,
            secret_key=secret_key,
        )

    files["app/extensions.py"] = _FLASK_TEMPLATES["app/extensions.py"].safe_substitute()
    files["app/auth/__init__.py"] = ""
    files["app/auth/routes.py"] = _FLASK_TEMPLATES["app/auth/routes.py"].safe_substitute()
    files["app/models/user.py"] = _FLASK_TEMPLATES["app/models/user.py"].safe_substitute()
    files["manage.py"] = _FLASK_TEMPLATES["manage.py"].safe_substitute()
    files[".env.example"] = _FLASK_TEMPLATES[".env.example"].safe_substitute(
        bundle_id=bundle_id,
        jwt_secret_key=jwt_secret_key,
        secret_key=secret_key,
    )

    # Package markers
    files["app/routes/__init__.py"] = ""

    # Per-entity rendering
    blueprint_registrations: list[str] = []
    blueprint_imports: list[str] = []
    model_imports: list[str] = []
    entity_list_lines: list[str] = []

    entities = (genome["modules"]["core"] or {}).get("entities") or {}
    for ent_name, ent_spec in entities.items():
        snake = _snake(ent_name)
        table = snake + "s"

        # SQLAlchemy column declarations
        cols: list[str] = []
        for fname, fspec in (ent_spec.get("fields") or {}).items():
            if fname == "id":
                continue
            cols.append(_column_for_field(fname, fspec))

        # Route field setters (create) and updaters (update)
        field_names = [
            fname for fname in (ent_spec.get("fields") or {})
            if fname != "id"
        ]
        field_setters = "\n".join(
            f"    obj.{fname} = data.get({fname!r})" for fname in field_names
        ) or "    pass  # no fields extracted"
        field_updaters = "\n".join(
            f"    if {fname!r} in data:\n        obj.{fname} = data[{fname!r}]"
            for fname in field_names
        ) or "    pass  # no fields"
        dict_fields = "\n".join(
            f'        "{fname}": obj.{fname},' for fname in field_names
        )

        # Model file
        files[f"app/models/{snake}.py"] = _FLASK_TEMPLATES[
            "app/models/_entity.py"
        ].safe_substitute(
            entity_name=ent_name,
            table_name=table,
            columns="\n".join(cols) if cols else "    pass  # no fields extracted",
        )

        # Blueprint/route file
        files[f"app/routes/{snake}.py"] = _FLASK_TEMPLATES[
            "app/routes/_entity.py"
        ].safe_substitute(
            entity_name=ent_name,
            snake_entity=snake,
            field_setters=field_setters,
            field_updaters=field_updaters,
            dict_fields=dict_fields,
        )

        model_imports.append(f"from app.models.{snake} import {ent_name}  # noqa: F401")
        blueprint_imports.append(
            f"    from app.routes.{snake} import {snake}_bp"
        )
        blueprint_registrations.append(
            f'    app.register_blueprint({snake}_bp, url_prefix="/api/{table}")'
        )
        entity_list_lines.append(f"- **{ent_name}**: {ent_spec.get('description', '')}")

    # app/models/__init__.py — imports every model so Flask-Migrate sees them
    files["app/models/__init__.py"] = _FLASK_TEMPLATES[
        "app/models/__init__.py"
    ].safe_substitute(
        model_imports="\n".join(model_imports),
    )

    # app/__init__.py — blueprint registration block
    reg_block = "\n".join(blueprint_imports + blueprint_registrations)
    files["app/__init__.py"] = _FLASK_TEMPLATES["app/__init__.py"].safe_substitute(
        bundle_id=bundle_id,
        blueprint_registrations=reg_block,
    )

    # Tests
    files["tests/test_app.py"] = _FLASK_TEMPLATES["tests/test_app.py"].safe_substitute(
        bundle_id=bundle_id,
    )
    files["tests/__init__.py"] = ""

    # App README (named README_app.md to avoid collision with package README)
    files["README.md"] = _FLASK_TEMPLATES["README_app.md"].safe_substitute(
        solution_name=name,
        entity_list="\n".join(entity_list_lines) or "_(no entities extracted from PRD)_",
    )

    # Genome for transparency
    files["GENOME.json"] = json.dumps(genome, indent=2, default=str)

    # Architecture documents
    files["ARCHITECTURE.md"] = _render_architecture_md(genome, entities)
    files["openapi.yaml"] = _render_openapi_yaml(genome, entities)

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


def microcodegen_flask(prd_text: str) -> bytes:
    """The complete algorithm. PRD text → Flask ZIP bytes."""
    manifest = parse_prd(prd_text)
    genome = manifest_to_genome(manifest)
    files = render_genome(genome)
    return pack(files)


# Keep microcodegen as an alias so callers can use the same name as the FastAPI package.
microcodegen = microcodegen_flask


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="archiet-microcodegen-flask: PRD text → Flask app ZIP."
    )
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
        zip_bytes = microcodegen_flask(prd_text)
        sys.stdout.buffer.write(zip_bytes)

    return 0


if __name__ == "__main__":
    sys.exit(main())
