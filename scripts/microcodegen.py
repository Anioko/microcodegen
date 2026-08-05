#!/usr/bin/env python3
"""microcodegen.py — Archiet's core algorithm in one file.

PRD text → manifest → genome → rendered FastAPI app → ZIP bytes.

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
    # Matches "## Entities", "Entities:", "## Data Models", or a numbered
    # "## 3. ENTITY DATA MODEL" — header introducing an entity section.
    # The optional leading "<n>." tolerates numbered section headings, and
    # "entity data model(s)" tolerates the OperateIQ-PRD phrasing.
    r"^#{1,3}\s*(?:\d+\.\s*)?"
    r"(?:entities|data models|domain models|entity list|entity data models?)"
    r"\s*:?\s*$",
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
            "archimate_type": "DataObject",  # ArchiMate 3.2 §9.3 Application Layer
        }

    # Build top-level ArchiMate element list for architecture doc generation.
    # ApplicationComponent = the Flask app itself.
    # DataObject = each entity (persisted data with identity).
    # ApplicationService = each external integration endpoint.
    # BusinessProcess = user stories that use workflow trigger verbs.
    _workflow_verbs = {
        "create",
        "update",
        "delete",
        "approve",
        "reject",
        "submit",
        "complete",
        "process",
        "generate",
        "schedule",
        "notify",
    }
    archimate_elements: list[dict] = [
        {
            "name": name,
            "type": "ApplicationComponent",
            "description": f"{name} FastAPI application",
        },
    ]
    for ent_name in entities_dict:
        archimate_elements.append(
            {
                "name": ent_name,
                "type": "DataObject",
                "description": entities_dict[ent_name]["description"],
            }
        )
    for story in manifest.get("user_stories", []):
        # User stories are dicts with as_a/i_want/so_that keys from parse_prd.
        text = story.get("i_want", story.get("story", "")).lower()
        if any(v in text for v in _workflow_verbs):
            label = text[:60]
            archimate_elements.append(
                {
                    "name": label,
                    "type": "BusinessProcess",
                    "description": f"I want to {text}",
                }
            )
    for intg in manifest.get("integrations", []):
        # Integrations are dicts with 'name' key from parse_prd.
        intg_name = intg.get("name", str(intg)) if isinstance(intg, dict) else str(intg)
        archimate_elements.append(
            {
                "name": intg_name,
                "type": "ApplicationService",
                "description": f"External integration: {intg_name}",
            }
        )

    return {
        "genome_version": "1.0.0",
        "solution_id": 0,
        "solution_name": name,
        "bundle_id": snake_name,
        "language": "fastapi",
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
        # OperateIQ semantic-graph spine (spec-level genome keys). The full
        # pipeline's genome_compiler flips semantic_graph to True when it sees a
        # SemanticRelationship entity and fills `projections` with the framework
        # projections whose source entities are present. The atomic algorithm
        # declares the keys with their inert defaults so the IR shape is honest.
        "semantic_graph": False,
        "projections": [],
    }


# ─── STAGE 3 ────────────────────────────────────────────────────────────────
# render_genome(genome) → {path: content}.
#
# Inline templates via string.Template. FastAPI + PostgreSQL + Alembic.
# The full pipeline branches across 12 stacks via the StackRenderer dispatch;
# this version is single-stack by design — adding a stack here would violate
# the "atomic algorithm" contract.


_FILE_TEMPLATES: dict[str, string.Template] = {
    "requirements.txt": string.Template("""\
fastapi>=0.110
uvicorn[standard]>=0.27
sqlalchemy>=2.0
alembic>=1.13
PyJWT>=2.8
bcrypt>=4.1
psycopg2-binary>=2.9
pydantic>=2.0
python-dotenv>=1.0
"""),
    "main.py": string.Template("""\
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import router as auth_router
$router_imports

from app.database import Base, engine
import app.models  # noqa: F401 -- registers every model on Base.metadata

# Create tables on first boot so `docker compose up` yields a working app with
# zero manual steps. Idempotent (no-op if the tables already exist). For
# versioned production migrations, use the bundled Alembic setup instead
# (alembic.ini + alembic/env.py): `alembic revision --autogenerate && alembic upgrade head`.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="$name", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
$router_includes


@app.get("/health")
def health():
    return {"status": "ok", "version": "$bundle_id"}
"""),
    "app/database.py": string.Template("""\
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Add it to .env or your deploy env.")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
"""),
    "app/auth.py": string.Template("""\
import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt as _jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User

_JWT_SECRET = os.environ.get("JWT_SECRET_KEY")
if not _JWT_SECRET:
    raise RuntimeError("JWT_SECRET_KEY is not set.")
_ALGORITHM = "HS256"
_EXPIRES_DAYS = 7

router = APIRouter()


def _hash(pw: str) -> str:
    # bcrypt hashes at most 72 bytes; truncate so longer passwords don't raise.
    return bcrypt.hashpw(pw.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def _verify(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))


def _token(user_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=_EXPIRES_DAYS)
    return _jwt.encode({"sub": user_id, "exp": exp}, _JWT_SECRET, algorithm=_ALGORITHM)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = _jwt.decode(token, _JWT_SECRET, algorithms=[_ALGORITHM])
        user_id: str = payload.get("sub")
    except _jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


class _AuthBody(BaseModel):
    email: str
    password: str


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "access_token", token,
        httponly=True, samesite="lax", max_age=_EXPIRES_DAYS * 86400,
    )


@router.post("/register", status_code=201)
def register(body: _AuthBody, response: Response, db: Session = Depends(get_db)):
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be >= 8 chars")
    if db.query(User).filter(User.email == body.email.lower()).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(id=str(uuid.uuid4()), email=body.email.lower(),
                password_hash=_hash(body.password))
    db.add(user)
    db.commit()
    _set_cookie(response, _token(user.id))
    return {"id": user.id, "email": user.email}


@router.post("/login")
def login(body: _AuthBody, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user or not _verify(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    _set_cookie(response, _token(user.id))
    return {"id": user.id, "email": user.email}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "email": current_user.email}
"""),
    "app/models/user.py": string.Template("""\
from datetime import datetime

from sqlalchemy import Column, DateTime, String

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
"""),
    "app/models/_entity.py": string.Template("""\
from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey
from sqlalchemy import Integer, JSON, Numeric, String, Text

from app.database import Base


class $entity_name(Base):
    __tablename__ = "$table_name"

    id = Column(String(36), primary_key=True)
    # Per-tenant ownership — every row is scoped to the user that created it.
    # Queries in the router filter by this to prevent cross-user data leaks.
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
$columns
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
"""),
    "app/schemas/_entity.py": string.Template("""\
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ${entity_name}Base(BaseModel):
$base_fields


class ${entity_name}Create(${entity_name}Base):
    pass


class ${entity_name}Update(BaseModel):
$optional_fields


class ${entity_name}Response(${entity_name}Base):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
"""),
    "app/routers/_entity.py": string.Template("""\
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.$snake_entity import $entity_name
from app.models.user import User
from app.schemas.$snake_entity import (
    ${entity_name}Create,
    ${entity_name}Update,
    ${entity_name}Response,
)

router = APIRouter()


@router.get("/", response_model=List[${entity_name}Response])
def list_${snake_entity}s(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query($entity_name).filter($entity_name.user_id == current_user.id).all()


@router.post("/", response_model=${entity_name}Response, status_code=201)
def create_$snake_entity(
    body: ${entity_name}Create,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = $entity_name(id=str(uuid.uuid4()), user_id=current_user.id, **body.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/{item_id}", response_model=${entity_name}Response)
def get_$snake_entity(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = db.query($entity_name).filter(
        $entity_name.id == item_id, $entity_name.user_id == current_user.id
    ).first()
    if not obj:
        raise HTTPException(status_code=404, detail="$entity_name not found")
    return obj


@router.put("/{item_id}", response_model=${entity_name}Response)
def update_$snake_entity(
    item_id: str,
    body: ${entity_name}Update,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = db.query($entity_name).filter(
        $entity_name.id == item_id, $entity_name.user_id == current_user.id
    ).first()
    if not obj:
        raise HTTPException(status_code=404, detail="$entity_name not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{item_id}", status_code=204)
def delete_$snake_entity(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = db.query($entity_name).filter(
        $entity_name.id == item_id, $entity_name.user_id == current_user.id
    ).first()
    if not obj:
        raise HTTPException(status_code=404, detail="$entity_name not found")
    db.delete(obj)
    db.commit()
"""),
    "alembic.ini": string.Template("""\
[alembic]
script_location = alembic
prepend_sys_path = .
version_path_separator = os

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""),
    "alembic/env.py": string.Template("""\
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.database import Base
import app.models  # noqa: F401 — registers all SQLAlchemy models with Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set.")
config.set_main_option("sqlalchemy.url", DATABASE_URL)


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
"""),
    ".env.example": string.Template("""\
DATABASE_URL=postgresql://archiet:archiet@localhost:5432/$bundle_id
JWT_SECRET_KEY=$jwt_secret_key
"""),
    "Dockerfile": string.Template("""\
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""),
    "docker-compose.yml": string.Template("""\
services:
  app:
    build: .
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: postgresql://archiet:archiet@db:5432/$bundle_id
      JWT_SECRET_KEY: $jwt_secret_key
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

Generated by microcodegen.py — FastAPI + PostgreSQL + Alembic.

## Quick start

```bash
cp .env.example .env
docker compose up
curl http://localhost:8000/health
# Interactive API docs: http://localhost:8000/docs
```

## End-to-end (register, login, CRUD)

```bash
# 1. Register (JWT set as httpOnly cookie)
curl -c cookies.txt -X POST http://localhost:8000/api/auth/register \\
     -H "Content-Type: application/json" \\
     -d '{"email":"you@example.com","password":"hunter22hunter"}'

# 2. Create an entity
curl -b cookies.txt -X POST http://localhost:8000/api/items/ \\
     -H "Content-Type: application/json" \\
     -d '{"name":"My first item"}'

# 3. List
curl -b cookies.txt http://localhost:8000/api/items/
```

## Entities

$entity_list

## Migrations

```bash
alembic revision --autogenerate -m "init"
alembic upgrade head
```

## What's included

- FastAPI + PostgreSQL (docker-compose with healthcheck-gated startup)
- JWT-cookie auth (httpOnly, SameSite=Lax): register / login / logout / me
- Pydantic v2 request/response validation with full type safety
- Full CRUD per entity: list, create, get, update, delete
- Per-tenant data isolation — every row scoped to the authenticated user
- Alembic migrations pre-configured
- /docs interactive OpenAPI UI (free, auto-generated by FastAPI)
- ARCHITECTURE.md + openapi.yaml shipped in this ZIP
"""),
    "tests/conftest.py": string.Template("""\
import os

os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-not-for-production-use")
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL",
                   "postgresql://archiet:archiet@localhost:5432/${bundle_id}_test"),
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from main import app  # noqa: E402


def _db_reachable() -> bool:
    try:
        e = create_engine(os.environ["DATABASE_URL"])
        with e.connect() as conn:
            conn.execute(text("SELECT 1"))  # tenant-exempt: connectivity probe
        return True
    except OperationalError:
        return False


@pytest.fixture(autouse=True, scope="session")
def require_db():
    if not _db_reachable():
        pytest.skip(
            "PostgreSQL not reachable — start it with `docker compose up -d db` "
            "or set TEST_DATABASE_URL and retry."
        )


@pytest.fixture
def app_client():
    test_engine = create_engine(os.environ["DATABASE_URL"])
    TestingSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    Base.metadata.drop_all(bind=test_engine)
    app.dependency_overrides.clear()
"""),
    "tests/test_health.py": string.Template("""\
def test_health(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
"""),
}


def _column_for_field(fname: str, fspec: dict) -> str:
    """SQLAlchemy Column() declaration for a single entity field."""
    type_map = {
        "string": "String(255)",
        "text": "Text",
        "integer": "Integer",
        "int": "Integer",
        "float": "Float",
        "decimal": "Numeric(12, 2)",
        "boolean": "Boolean",
        "bool": "Boolean",
        "datetime": "DateTime",
        "date": "Date",
        "uuid": "String(36)",
        "json": "JSON",
    }
    sa_type = type_map.get(fspec.get("type", "string"), "String(255)")
    nullable = "" if fspec.get("required") else ", nullable=True"
    unique = ", unique=True" if fspec.get("unique") else ""
    indexed = ", index=True" if fspec.get("indexed") else ""
    return f"    {fname} = Column({sa_type}{nullable}{unique}{indexed})"


def _pydantic_type_for_field(fspec: dict) -> str:
    """Pydantic v2 type annotation string for a single entity field."""
    type_map = {
        "string": "str",
        "text": "str",
        "integer": "int",
        "int": "int",
        "float": "float",
        "decimal": "float",
        "boolean": "bool",
        "bool": "bool",
        "datetime": "datetime",
        "date": "str",
        "uuid": "str",
        "json": "dict",
    }
    return type_map.get(fspec.get("type", "string"), "str")


def _render_architecture_md(genome: dict, entities: dict) -> str:
    """Generate ARCHITECTURE.md showing ArchiMate 3.2 element types.

    Gives the developer downloading the ZIP a typed map of what was
    generated and how each piece relates. Mirrors what the full Archiet
    platform emits from the formal ArchiMate model, but derived
    heuristically from the PRD text.
    """
    name = genome["solution_name"]
    elements = genome.get("archimate_elements", [])
    user_stories = genome.get("user_stories", [])
    integrations = genome.get("integrations", [])

    lines: list[str] = [
        f"# Architecture — {name}",
        "",
        "Generated by microcodegen.py · ArchiMate 3.2 element notation",
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
        for story in user_stories[:10]:  # cap to keep doc readable
            lines.append(
                f"- As a {story.get('as_a', '')}, I want to {story.get('i_want', '')}"
            )

    lines += [
        "",
        "## Notes",
        "",
        "- This file is heuristically derived from PRD text.",
        "- The full Archiet platform generates a formal ArchiMate 3.2 model",
        "  (ApplicationComponent, DataObject, BusinessProcess, ApplicationService,",
        "  AssignmentRelationship, RealizationRelationship) from the genome IR,",
        "  plus DMN 1.5 decision tables, BPMN 2.0 process diagrams, and a",
        "  complete openapi.yaml verified against the running application.",
        "- To regenerate: edit GENOME.json and re-run microcodegen.py,",
        "  or use the Archiet platform for cross-stack + formal-model output.",
    ]
    return "\n".join(lines) + "\n"


def _render_openapi_yaml(genome: dict, entities: dict) -> str:
    """Generate openapi.yaml (OpenAPI 3.1) from entity/route structure.

    Emits one path group per entity (list + detail CRUD) plus the auth
    endpoints. Field types are mapped from the genome's SQLAlchemy-style
    type strings to OpenAPI primitive types.
    """
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
        f"  description: Generated by microcodegen.py for {name}",
        "  version: 0.1.0",
        "servers:",
        "  - url: http://localhost:8000",
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
            f"  /api/{plural}:",
            "    get:",
            f"      summary: List {ent_name} records",
            f"      tags: [{ent_name}]",
            "      security: [{bearerAuth: []}]",
            "      responses:",
            f"        '200': {{description: List of {ent_name}}}",
            "    post:",
            f"      summary: Create {ent_name}",
            f"      tags: [{ent_name}]",
            "      security: [{bearerAuth: []}]",
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
            "      security: [{bearerAuth: []}]",
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
            "      security: [{bearerAuth: []}]",
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
            "      security: [{bearerAuth: []}]",
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
        "    bearerAuth:",
        "      type: http",
        "      scheme: bearer",
        "      bearerFormat: JWT",
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

    The output is what the customer would download. FastAPI + PostgreSQL.
    """
    bundle_id = genome["bundle_id"]
    name = genome["solution_name"]
    # Per-ZIP random secret so two customers cannot forge tokens cross-ZIP.
    jwt_secret_key = secrets.token_urlsafe(32)
    files: dict[str, str] = {}

    # Fixed files (no per-entity substitution beyond bundle_id / secrets)
    for path in ("requirements.txt", "Dockerfile", "docker-compose.yml", "alembic.ini"):
        files[path] = _FILE_TEMPLATES[path].safe_substitute(
            bundle_id=bundle_id, jwt_secret_key=jwt_secret_key
        )

    # Auth + database scaffold (always emitted — CRUD routes depend on it)
    files["app/database.py"] = _FILE_TEMPLATES["app/database.py"].safe_substitute()
    files["app/auth.py"] = _FILE_TEMPLATES["app/auth.py"].safe_substitute()
    files["app/models/user.py"] = _FILE_TEMPLATES[
        "app/models/user.py"
    ].safe_substitute()
    files["alembic/env.py"] = _FILE_TEMPLATES["alembic/env.py"].safe_substitute()

    # Package markers
    files["app/routers/__init__.py"] = ""
    files["app/schemas/__init__.py"] = ""

    # Per-entity files
    router_imports: list[str] = []
    router_includes: list[str] = []
    model_imports: list[str] = ["from app.models.user import User  # noqa: F401"]
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

        # Pydantic base + update field declarations
        base_fields: list[str] = []
        optional_fields: list[str] = []
        for fname, fspec in (ent_spec.get("fields") or {}).items():
            if fname == "id":
                continue
            py_type = _pydantic_type_for_field(fspec)
            if fspec.get("required"):
                base_fields.append(f"    {fname}: {py_type}")
            else:
                base_fields.append(f"    {fname}: Optional[{py_type}] = None")
            optional_fields.append(f"    {fname}: Optional[{py_type}] = None")

        files[f"app/models/{snake}.py"] = _FILE_TEMPLATES[
            "app/models/_entity.py"
        ].safe_substitute(
            entity_name=ent_name,
            table_name=table,
            columns="\n".join(cols) if cols else "    pass  # no fields extracted",
        )
        files[f"app/schemas/{snake}.py"] = _FILE_TEMPLATES[
            "app/schemas/_entity.py"
        ].safe_substitute(
            entity_name=ent_name,
            base_fields="\n".join(base_fields) if base_fields else "    pass",
            optional_fields="\n".join(optional_fields)
            if optional_fields
            else "    pass",
        )
        files[f"app/routers/{snake}.py"] = _FILE_TEMPLATES[
            "app/routers/_entity.py"
        ].safe_substitute(entity_name=ent_name, snake_entity=snake)

        model_imports.append(f"from app.models.{snake} import {ent_name}  # noqa: F401")
        router_imports.append(
            f"from app.routers.{snake} import router as {snake}_router"
        )
        router_includes.append(
            f'app.include_router({snake}_router, prefix="/api/{table}", tags=["{ent_name}"])'
        )
        entity_list_lines.append(f"- **{ent_name}**: {ent_spec.get('description', '')}")

    # app/models/__init__.py imports every model so Alembic discovers them all
    files["app/models/__init__.py"] = "\n".join(model_imports) + "\n"

    files["main.py"] = _FILE_TEMPLATES["main.py"].safe_substitute(
        name=name,
        bundle_id=bundle_id,
        router_imports="\n".join(router_imports),
        router_includes="\n".join(router_includes),
    )
    files[".env.example"] = _FILE_TEMPLATES[".env.example"].safe_substitute(
        bundle_id=bundle_id, jwt_secret_key=jwt_secret_key
    )
    files["tests/conftest.py"] = _FILE_TEMPLATES["tests/conftest.py"].safe_substitute(
        bundle_id=bundle_id
    )
    files["tests/test_health.py"] = _FILE_TEMPLATES[
        "tests/test_health.py"
    ].safe_substitute()
    files["README.md"] = _FILE_TEMPLATES["README.md"].safe_substitute(
        solution_name=name,
        entity_list="\n".join(entity_list_lines)
        or "_(no entities extracted from PRD)_",
    )

    # Genome for transparency
    files["GENOME.json"] = json.dumps(genome, indent=2, default=str)

    # Architecture documents — what separates Archiet from a CRUD generator.
    # ARCHITECTURE.md gives the developer a typed ArchiMate element map.
    # openapi.yaml gives them a machine-readable API contract they can import
    # into Postman, Swagger UI, or a client generator immediately.
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
