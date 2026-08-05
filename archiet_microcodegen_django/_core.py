#!/usr/bin/env python3
"""_core.py — archiet-microcodegen-django.

PRD text → manifest → genome → Django REST Framework app → ZIP bytes.

Contract:  microcodegen_django(prd_text) → bytes  (working, bootable Django ZIP)

  # CLI:  python -m archiet_microcodegen_django prd.md > app.zip
  #       python -m archiet_microcodegen_django prd.md --out /tmp/myapp/
  # Lib:  from archiet_microcodegen_django import microcodegen_django

Stages:
  1. parse_prd(text) → manifest dict   (copied verbatim from microcodegen.py)
  2. manifest_to_genome(manifest) → genome dict  (copied verbatim)
  3. render_genome(genome) → {path: content}  (Django DRF rendering — THIS FILE)
  4. pack(files) → bytes   (stdlib zipfile — copied verbatim)

Auth contract (non-negotiable):
  - JWT via httpOnly cookies: Set-Cookie: access_token=...; HttpOnly; SameSite=Lax
  - NEVER localStorage, NEVER Authorization: Bearer in response body
  - Login: POST /api/auth/login → sets httpOnly cookie, returns {user: {...}}
  - Protected endpoints: JWTCookieAuthentication reads cookie

Constraints:
  - Pure stdlib; zero app.* / agents.* / templates/ imports in this file.
  - Hard ceiling: 1400 LOC.
  - No LLM calls — purely deterministic regex + string.Template rendering.
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
# Copied verbatim from scripts/microcodegen.py — language-agnostic.

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
    r"As\s+(?:a|an)\s+([^,]+?),\s+I\s+want\s+(?:to\s+)?([^,]+?)(?:,?\s*so\s+that\s+([^.]+))?\.{1}",
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
    m = re.match(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return "Generated App"


def parse_prd(text: str) -> dict:
    """Extract a manifest dict from raw PRD text. Verbatim from microcodegen.py."""
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
                entity_section[m.start(): m.end()] + ent_body.split("\n", 1)[0]
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
# Copied verbatim from scripts/microcodegen.py — language-agnostic.


def _snake(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.strip()).strip("_")
    s = re.sub(r"([a-z])([A-Z])", r"\1_\2", s)
    return s.lower()


def manifest_to_genome(manifest: dict) -> dict:
    """Map the heuristic manifest into the canonical genome shape. Verbatim from microcodegen.py."""
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
            "description": f"{ent['name']} entity (generated by microcodegen-django)",
            "archimate_type": "DataObject",
        }

    _workflow_verbs = {
        "create", "update", "delete", "approve", "reject", "submit",
        "complete", "process", "generate", "schedule", "notify",
    }
    archimate_elements: list[dict] = [
        {"name": name, "type": "ApplicationComponent",
         "description": f"{name} Django application"},
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
        "language": "django",
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
# Django REST Framework + PostgreSQL. Written from scratch for Django idioms.
# Auth: httpOnly JWT cookies via JWTCookieAuthentication.
# Per-entity: Model + ModelSerializer + ModelViewSet + DefaultRouter.


_DJANGO_TYPE_MAP = {
    "string": "models.CharField(max_length=255)",
    "text": "models.TextField()",
    "integer": "models.IntegerField()",
    "int": "models.IntegerField()",
    "float": "models.FloatField()",
    "decimal": "models.DecimalField(max_digits=12, decimal_places=2)",
    "boolean": "models.BooleanField(default=False)",
    "bool": "models.BooleanField(default=False)",
    "datetime": "models.DateTimeField()",
    "date": "models.DateField()",
    "uuid": "models.UUIDField()",
    "json": "models.JSONField(default=dict)",
}

_OPENAPI_TYPE_MAP = {
    "string": "string", "text": "string", "integer": "integer",
    "int": "integer", "float": "number", "decimal": "number",
    "boolean": "boolean", "bool": "boolean", "datetime": "string",
    "date": "string", "uuid": "string", "json": "object",
}

# ── Fixed file templates ──────────────────────────────────────────────────────

_T_MANAGE = string.Template("""\
#!/usr/bin/env python
"""
+ '"""Django management script — generated by archiet-microcodegen-django."""'
+ """

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "${project}.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Activate your virtualenv and "
            "run: pip install -r requirements.txt"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
""")

_T_SETTINGS = string.Template("""\
# ${project}/settings.py — generated by archiet-microcodegen-django
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "DJANGO_SECRET_KEY is not set. Copy .env.example -> .env. "
        'Rotate with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "corsheaders",
    "apps.accounts",
$entity_installed_apps
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "${project}.urls"

_db_url = os.environ.get("DATABASE_URL")
if not _db_url:
    raise RuntimeError("DATABASE_URL is not set. This application requires PostgreSQL.")
if "sqlite" in _db_url.lower():
    raise RuntimeError("SQLite is not supported. Use PostgreSQL.")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "${bundle_id}"),
        "USER": os.environ.get("DB_USER", "archiet"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "archiet"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.JWTCookieAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME_HOURS": 24,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
}

CORS_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:8000"
).split(",")
CORS_ALLOW_CREDENTIALS = True

USE_TZ = True
TIME_ZONE = "UTC"
""")

_T_URLS = string.Template("""\
# ${project}/urls.py — generated by archiet-microcodegen-django
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.accounts import views as auth_views
$entity_router_imports

router = DefaultRouter()
$entity_router_registrations

urlpatterns = [
    path("api/", include(router.urls)),
    path("api/auth/register", auth_views.register, name="auth-register"),
    path("api/auth/login", auth_views.login, name="auth-login"),
    path("api/auth/logout", auth_views.logout, name="auth-logout"),
    path("api/auth/me", auth_views.me, name="auth-me"),
    path("health/", auth_views.health, name="health"),
]
""")

_T_WSGI = string.Template("""\
# ${project}/wsgi.py — generated by archiet-microcodegen-django
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "${project}.settings")
application = get_wsgi_application()
""")

_T_SETTINGS_INIT = string.Template("""\
# ${project}/__init__.py
""")

_T_REQUIREMENTS = string.Template("""\
Django>=5.0,<6.0
djangorestframework>=3.15
djangorestframework-simplejwt>=5.3
django-cors-headers>=4.3
psycopg2-binary>=2.9
gunicorn>=22.0
PyJWT>=2.8
python-dotenv>=1.0
""")

_T_DOCKERFILE = string.Template("""\
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["gunicorn", "${project}.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
""")

_T_DOCKER_COMPOSE = string.Template("""\
services:
  app:
    build: .
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: postgresql://archiet:archiet@db:5432/${bundle_id}
      DB_HOST: db
      DB_NAME: ${bundle_id}
      DB_USER: archiet
      DB_PASSWORD: archiet
      DJANGO_SECRET_KEY: ${django_secret_key}
      DJANGO_DEBUG: "false"
    depends_on:
      db:
        condition: service_healthy
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: archiet
      POSTGRES_PASSWORD: archiet
      POSTGRES_DB: ${bundle_id}
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U archiet -d ${bundle_id}"]
      interval: 3s
      timeout: 3s
      retries: 20
volumes:
  pgdata:
""")

_T_ENV_EXAMPLE = string.Template("""\
DATABASE_URL=postgresql://archiet:archiet@localhost:5432/${bundle_id}
DB_HOST=localhost
DB_NAME=${bundle_id}
DB_USER=archiet
DB_PASSWORD=archiet
DJANGO_SECRET_KEY=${django_secret_key}
DJANGO_DEBUG=false
CORS_ORIGINS=http://localhost:3000
""")

# ── accounts app ────────────────────────────────────────────────────────────

_T_ACCOUNTS_INIT = string.Template("# apps/accounts/__init__.py\n")

_T_ACCOUNTS_APPS = string.Template("""\
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
""")

_T_ACCOUNTS_MODELS = string.Template("""\
# apps/accounts/models.py — generated by archiet-microcodegen-django
import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email, password=None):
        if not email:
            raise ValueError("Email required")
        user = self.model(email=self.normalize_email(email))
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        db_table = "accounts_user"

    def __str__(self):
        return self.email
""")

_T_ACCOUNTS_AUTH = string.Template("""\
# apps/accounts/authentication.py — httpOnly JWT cookie auth
# JWT is read from the httpOnly cookie, never from Authorization header.
import os
import jwt
from rest_framework import authentication, exceptions
from apps.accounts.models import User


class JWTCookieAuthentication(authentication.BaseAuthentication):
    \"\"\"Reads JWT from httpOnly cookie set by the login view.
    Never reads Authorization header — cookie-only auth prevents XSS token theft.
    \"\"\"
    cookie_name = "access_token"

    def authenticate(self, request):
        token = request.COOKIES.get(self.cookie_name)
        if not token:
            return None
        secret = os.environ.get("DJANGO_SECRET_KEY")
        if not secret:
            raise exceptions.AuthenticationFailed("DJANGO_SECRET_KEY not configured")
        try:
            payload = jwt.decode(token, secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed("Token expired") from None
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed("Invalid token") from None
        user_id = payload.get("user_id")
        if not user_id:
            raise exceptions.AuthenticationFailed("Token missing user_id claim")
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise exceptions.AuthenticationFailed("User not found") from None
        if not user.is_active:
            raise exceptions.AuthenticationFailed("User account disabled")
        return (user, token)

    def authenticate_header(self, request):
        return "Cookie"
""")

_T_ACCOUNTS_VIEWS = string.Template("""\
# apps/accounts/views.py — Auth views: register / login / logout / me / health
# JWT is set as httpOnly cookie. NEVER returned in response body.
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from apps.accounts.models import User

_JWT_EXPIRY_HOURS = 24


def _secret():
    s = os.environ.get("DJANGO_SECRET_KEY", "")
    if not s:
        raise RuntimeError("DJANGO_SECRET_KEY not set")
    return s


def _make_token(user):
    payload = {
        "user_id": str(user.id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def _set_cookie(response, token):
    response.set_cookie(
        "access_token",
        token,
        httponly=True,
        secure=os.environ.get("DJANGO_DEBUG", "false").lower() != "true",
        samesite="Lax",
        max_age=_JWT_EXPIRY_HOURS * 3600,
    )
    return response


@csrf_exempt
@require_POST
def register(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not email or not password:
        return JsonResponse({"error": "email and password required"}, status=400)
    if len(password) < 8:
        return JsonResponse({"error": "Password must be >= 8 characters"}, status=400)
    if User.objects.filter(email=email).exists():
        return JsonResponse({"error": "Email already registered"}, status=409)
    user = User.objects.create_user(email=email, password=password)
    token = _make_token(user)
    resp = JsonResponse(
        {"user": {"id": str(user.id), "email": user.email}}, status=201
    )
    return _set_cookie(resp, token)


@csrf_exempt
@require_POST
def login(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return JsonResponse({"error": "Invalid credentials"}, status=401)
    if not user.check_password(password):
        return JsonResponse({"error": "Invalid credentials"}, status=401)
    if not user.is_active:
        return JsonResponse({"error": "Account disabled"}, status=403)
    token = _make_token(user)
    resp = JsonResponse({"user": {"id": str(user.id), "email": user.email}})
    return _set_cookie(resp, token)


@csrf_exempt
@require_POST
def logout(request):
    resp = JsonResponse({"ok": True})
    resp.delete_cookie("access_token")
    return resp


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    u = request.user
    return JsonResponse({"user": {"id": str(u.id), "email": u.email}})


def health(request):
    return JsonResponse({"status": "ok", "version": "${bundle_id}"})
""")

_T_ACCOUNTS_URLS = string.Template("""\
# apps/accounts/urls.py
from django.urls import path
from apps.accounts import views

urlpatterns = [
    path("register", views.register, name="auth-register"),
    path("login", views.login, name="auth-login"),
    path("logout", views.logout, name="auth-logout"),
    path("me", views.me, name="auth-me"),
]
""")

# ── Per-entity templates ─────────────────────────────────────────────────────

_T_ENTITY_MODELS = string.Template("""\
# apps/${snake_entity}/models.py — generated by archiet-microcodegen-django
import uuid
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class ${entity_name}(models.Model):
    \"\"\"${description}\"\"\"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Per-tenant ownership — every row is scoped to the user that created it.
    # Queries in the ViewSet filter by this to prevent cross-user data leaks.
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="${snake_entity}_set"
    )
$field_declarations
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "${table_name}"
        ordering = ["-created_at"]

    def __str__(self):
        return f"${entity_name}({self.id})"
""")

_T_ENTITY_SERIALIZERS = string.Template("""\
# apps/${snake_entity}/serializers.py — generated by archiet-microcodegen-django
from rest_framework import serializers
from apps.${snake_entity}.models import ${entity_name}


class ${entity_name}Serializer(serializers.ModelSerializer):
    class Meta:
        model = ${entity_name}
        fields = "__all__"
        read_only_fields = ("id", "user", "created_at", "updated_at")
""")

_T_ENTITY_VIEWS = string.Template("""\
# apps/${snake_entity}/views.py — generated by archiet-microcodegen-django
from rest_framework import viewsets, permissions
from apps.${snake_entity}.models import ${entity_name}
from apps.${snake_entity}.serializers import ${entity_name}Serializer


class ${entity_name}ViewSet(viewsets.ModelViewSet):
    \"\"\"CRUD for ${entity_name}. All rows scoped to request.user.\"\"\"
    serializer_class = ${entity_name}Serializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Tenant isolation: only return rows owned by the authenticated user.
        return ${entity_name}.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
""")

_T_ENTITY_URLS = string.Template("""\
# apps/${snake_entity}/urls.py — generated by archiet-microcodegen-django
from rest_framework.routers import DefaultRouter
from apps.${snake_entity}.views import ${entity_name}ViewSet

router = DefaultRouter()
router.register(r"${snake_entity}s", ${entity_name}ViewSet, basename="${snake_entity}")

urlpatterns = router.urls
""")

_T_ENTITY_APPS = string.Template("""\
from django.apps import AppConfig


class ${entity_name}Config(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.${snake_entity}"
""")


def _render_architecture_md(genome: dict, entities: dict) -> str:
    name = genome["solution_name"]
    elements = genome.get("archimate_elements", [])
    user_stories = genome.get("user_stories", [])
    integrations = genome.get("integrations", [])

    lines: list[str] = [
        f"# Architecture — {name}",
        "",
        "Generated by archiet-microcodegen-django · ArchiMate 3.2 element notation",
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
        "## Django DRF Component Map",
        "",
        "| Entity | ApplicationComponent (ViewSet) | DataObject (Model) | ApplicationService (Serializer) |",
        "|--------|-------------------------------|-------------------|--------------------------------|",
    ]
    for ent_name in entities:
        snake = _snake(ent_name)
        lines.append(
            f"| {ent_name} | `{ent_name}ViewSet` | `{ent_name}` | `{ent_name}Serializer` |"
        )

    lines += ["", "## Relationships", "", "```", f"  {name} (ApplicationComponent)"]
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
        "## Notes",
        "",
        "- Heuristically derived from PRD text.",
        "- The full Archiet platform generates a formal ArchiMate 3.2 model,",
        "  DMN 1.5 decision tables, BPMN 2.0 process diagrams, and a",
        "  complete openapi.yaml verified against the running application.",
        "- To regenerate: edit GENOME.json and re-run archiet-microcodegen-django.",
    ]
    return "\n".join(lines) + "\n"


def _render_openapi_yaml(genome: dict, entities: dict) -> str:
    name = genome["solution_name"]

    lines: list[str] = [
        "openapi: '3.1.0'",
        "info:",
        f"  title: {name} API",
        f"  description: Generated by archiet-microcodegen-django for {name}",
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
        "        '201': {description: User created, JWT set in httpOnly cookie}",
        "        '409': {description: Email already registered}",
        "  /api/auth/login:",
        "    post:",
        "      summary: Login — JWT set as httpOnly cookie, never in response body",
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
        "  /health/:",
        "    get:",
        "      summary: Health check",
        "      tags: [ops]",
        "      responses:",
        "        '200': {description: OK}",
    ]

    for ent_name, ent_spec in entities.items():
        snake = _snake(ent_name)
        plural = snake + "s"
        lines += [
            f"  /api/{plural}/:",
            "    get:",
            f"      summary: List {ent_name} records (scoped to authenticated user)",
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
            f"  /api/{plural}/{{id}}/:",
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
        "      name: access_token",
        "      description: httpOnly JWT cookie — set by /api/auth/login",
        "  schemas:",
        "    AuthRequest:",
        "      type: object",
        "      required: [email, password]",
        "      properties:",
        "        email: {type: string, format: email}",
        "        password: {type: string, format: password}",
    ]
    for ent_name, ent_spec in entities.items():
        lines += [f"    {ent_name}:", "      type: object", "      properties:"]
        for fname, fspec in (ent_spec.get("fields") or {}).items():
            if fname == "id":
                continue
            oa_type = _OPENAPI_TYPE_MAP.get(fspec.get("type", "string"), "string")
            lines.append(f"        {fname}: {{type: {oa_type}}}")

    return "\n".join(lines) + "\n"


def _django_field(fname: str, fspec: dict) -> str:
    """Return a Django model field declaration line."""
    base_type = fspec.get("type", "string")
    field = _DJANGO_TYPE_MAP.get(base_type, "models.CharField(max_length=255)")
    # Strip closing paren to add kwargs
    field_open = field.rstrip(")")
    kwargs = []
    if not fspec.get("required"):
        kwargs.append("null=True, blank=True")
    if fspec.get("unique"):
        kwargs.append("unique=True")
    if fspec.get("indexed"):
        kwargs.append("db_index=True")
    if kwargs:
        separator = ", " if "(" in field_open and field_open.endswith("(") else ", "
        # Handle fields that have content already inside parens
        if field_open.endswith("("):
            return f"    {fname} = {field_open}{', '.join(kwargs)})"
        else:
            return f"    {fname} = {field_open}, {', '.join(kwargs)})"
    return f"    {fname} = {field}"


def render_genome(genome: dict) -> dict[str, str]:
    """Render the genome into a {path: content} dict.

    Django REST Framework + PostgreSQL. httpOnly JWT cookies for auth.
    Per-entity: Model + Serializer + ViewSet + DefaultRouter.
    """
    bundle_id = genome["bundle_id"]
    name = genome["solution_name"]
    project = bundle_id  # Django project package name
    django_secret_key = secrets.token_urlsafe(48)
    files: dict[str, str] = {}

    entities = (genome["modules"]["core"] or {}).get("entities") or {}

    # ── Project package ──────────────────────────────────────────────────────
    files["manage.py"] = _T_MANAGE.safe_substitute(project=project)
    files[f"{project}/__init__.py"] = _T_SETTINGS_INIT.safe_substitute(project=project)
    files[f"{project}/wsgi.py"] = _T_WSGI.safe_substitute(project=project)

    # Build entity_installed_apps for settings.py
    entity_installed_apps = "\n".join(
        f'    "apps.{_snake(ent_name)}",' for ent_name in entities
    )
    files[f"{project}/settings.py"] = _T_SETTINGS.safe_substitute(
        project=project,
        bundle_id=bundle_id,
        entity_installed_apps=entity_installed_apps,
        django_secret_key=django_secret_key,
    )

    # Build urls.py with per-entity router registrations
    entity_router_imports = "\n".join(
        f"from apps.{_snake(e)}.views import {e}ViewSet" for e in entities
    )
    entity_router_registrations = "\n".join(
        f'router.register(r"{_snake(e)}s", {e}ViewSet, basename="{_snake(e)}")'
        for e in entities
    )
    files[f"{project}/urls.py"] = _T_URLS.safe_substitute(
        project=project,
        entity_router_imports=entity_router_imports,
        entity_router_registrations=entity_router_registrations,
    )

    # ── Fixed files ──────────────────────────────────────────────────────────
    files["requirements.txt"] = _T_REQUIREMENTS.safe_substitute()
    files["Dockerfile"] = _T_DOCKERFILE.safe_substitute(project=project)
    files["docker-compose.yml"] = _T_DOCKER_COMPOSE.safe_substitute(
        bundle_id=bundle_id,
        django_secret_key=django_secret_key,
    )
    files[".env.example"] = _T_ENV_EXAMPLE.safe_substitute(
        bundle_id=bundle_id,
        django_secret_key=django_secret_key,
    )

    # ── accounts app ─────────────────────────────────────────────────────────
    files["apps/__init__.py"] = ""
    files["apps/accounts/__init__.py"] = _T_ACCOUNTS_INIT.safe_substitute()
    files["apps/accounts/apps.py"] = _T_ACCOUNTS_APPS.safe_substitute()
    files["apps/accounts/models.py"] = _T_ACCOUNTS_MODELS.safe_substitute()
    files["apps/accounts/authentication.py"] = _T_ACCOUNTS_AUTH.safe_substitute()
    files["apps/accounts/views.py"] = _T_ACCOUNTS_VIEWS.safe_substitute(
        bundle_id=bundle_id
    )
    files["apps/accounts/urls.py"] = _T_ACCOUNTS_URLS.safe_substitute()

    # ── Per-entity files ─────────────────────────────────────────────────────
    entity_list_lines: list[str] = []
    for ent_name, ent_spec in entities.items():
        snake = _snake(ent_name)
        table = snake + "s"
        description = ent_spec.get("description", f"{ent_name} entity")

        field_lines: list[str] = []
        for fname, fspec in (ent_spec.get("fields") or {}).items():
            if fname == "id":
                continue
            field_lines.append(_django_field(fname, fspec))

        field_declarations = (
            "\n".join(field_lines) if field_lines else "    # no fields extracted from PRD"
        )

        files[f"apps/{snake}/__init__.py"] = ""
        files[f"apps/{snake}/apps.py"] = _T_ENTITY_APPS.safe_substitute(
            entity_name=ent_name, snake_entity=snake
        )
        files[f"apps/{snake}/models.py"] = _T_ENTITY_MODELS.safe_substitute(
            entity_name=ent_name,
            snake_entity=snake,
            table_name=table,
            description=description,
            field_declarations=field_declarations,
        )
        files[f"apps/{snake}/serializers.py"] = _T_ENTITY_SERIALIZERS.safe_substitute(
            entity_name=ent_name, snake_entity=snake
        )
        files[f"apps/{snake}/views.py"] = _T_ENTITY_VIEWS.safe_substitute(
            entity_name=ent_name, snake_entity=snake
        )
        files[f"apps/{snake}/urls.py"] = _T_ENTITY_URLS.safe_substitute(
            entity_name=ent_name, snake_entity=snake
        )
        entity_list_lines.append(f"- **{ent_name}**: {description}")

    # ── Architecture documents ───────────────────────────────────────────────
    files["ARCHITECTURE.md"] = _render_architecture_md(genome, entities)
    files["openapi.yaml"] = _render_openapi_yaml(genome, entities)
    files["GENOME.json"] = json.dumps(genome, indent=2, default=str)

    # ── App README ───────────────────────────────────────────────────────────
    entity_list = "\n".join(entity_list_lines) or "_(no entities extracted from PRD)_"
    files["README.md"] = _T_APP_README.safe_substitute(
        solution_name=name,
        bundle_id=bundle_id,
        entity_list=entity_list,
    )

    return files


_T_APP_README = string.Template("""\
# ${solution_name}

Generated by [archiet-microcodegen-django](https://archiet.com?utm_source=pypi&utm_medium=package&utm_campaign=microcodegen-django) — Django REST Framework + PostgreSQL.

## Quick start

```bash
cp .env.example .env
docker compose up
curl http://localhost:8000/health/
```

## Auth (httpOnly cookies — never localStorage)

```bash
# Register
curl -c cookies.txt -X POST http://localhost:8000/api/auth/register \\
     -H "Content-Type: application/json" \\
     -d '{"email":"you@example.com","password":"hunter22hunter"}'

# Login
curl -c cookies.txt -X POST http://localhost:8000/api/auth/login \\
     -H "Content-Type: application/json" \\
     -d '{"email":"you@example.com","password":"hunter22hunter"}'

# Create an entity (cookie sent automatically)
curl -b cookies.txt -X POST http://localhost:8000/api/items/ \\
     -H "Content-Type: application/json" \\
     -d '{"name":"My first item"}'

# List (tenant-scoped — only your rows)
curl -b cookies.txt http://localhost:8000/api/items/
```

## Entities

${entity_list}

## Migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

## What's included

- Django 5 + Django REST Framework (ModelViewSet full CRUD per entity)
- JWT auth via httpOnly SameSite=Lax cookies — never localStorage
- Per-tenant data isolation — every row has a `user` FK; every ViewSet filters by `request.user`
- Custom User model (UUID primary key, email login)
- JWTCookieAuthentication class wired into REST_FRAMEWORK settings
- docker-compose.yml — Postgres 16 with healthcheck-gated startup
- Dockerfile — gunicorn production server
- ARCHITECTURE.md — ArchiMate 3.2 element map
- openapi.yaml — machine-readable API contract

Zero LLM calls. Zero API keys. Pure Python stdlib generator.

Built on [Archiet](https://archiet.com?utm_source=pypi&utm_medium=package&utm_campaign=microcodegen-django).
""")


# ─── STAGE 4 ────────────────────────────────────────────────────────────────
# pack(files) → bytes. Copied verbatim from scripts/microcodegen.py.


def pack(files: dict[str, str]) -> bytes:
    """Pack {path: content} into ZIP bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in sorted(files.items()):
            zf.writestr(path, content)
    return buf.getvalue()


# ─── PUBLIC ENTRY ───────────────────────────────────────────────────────────


def microcodegen_django(prd_text: str) -> bytes:
    """The complete algorithm. PRD text → Django DRF ZIP bytes."""
    manifest = parse_prd(prd_text)
    genome = manifest_to_genome(manifest)
    files = render_genome(genome)
    return pack(files)


# Alias so callers can use `microcodegen` as the verb (consistent with FastAPI package)
microcodegen = microcodegen_django


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="archiet-microcodegen-django: PRD text → Django DRF app ZIP."
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
        zip_bytes = microcodegen_django(prd_text)
        sys.stdout.buffer.write(zip_bytes)

    return 0


if __name__ == "__main__":
    sys.exit(main())
