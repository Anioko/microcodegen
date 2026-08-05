# Publishing

This repository is the single source for every microcodegen package. It was
consolidated here on 2026-08-05 from `aniekanasuquookono-web/archiet`, which is
being archived — and archiving stops GitHub Actions, so these pipelines had to
move or the live packages would have frozen.

## Releases are tag-triggered

Each package publishes on its own tag prefix. Nothing publishes on a push to
`main`.

| Tag | Package | Registry | Source |
|---|---|---|---|
| *(see workflow)* | `archiet-microcodegen` | PyPI | `scripts/microcodegen.py` |
| `django-v*.*.*` | `archiet-microcodegen-django` | PyPI | `archiet_microcodegen_django/` |
| `flask-v*.*.*` | `archiet-microcodegen-flask` | PyPI | `archiet_microcodegen_flask/` |
| `mcp-v*.*.*` | `mcp-archiet` | PyPI | *(MCP server)* |
| `nestjs-v*.*.*` | `archiet-microcodegen-nestjs` | npm | `archiet_microcodegen_nestjs/` |
| `tauri-v*` | `archiet-microcodegen-tauri` | crates.io | `archiet_microcodegen_tauri/` |
| `dotnet-v*.*.*` | `archiet-microcodegen-dotnet` | NuGet | `archiet_microcodegen_dotnet/` |
| `rails-v*.*.*` | `archiet-microcodegen-rails` | RubyGems | `archiet_microcodegen_rails/` |
| `java-v*.*.*` | `archiet-microcodegen-java` | GitHub release | `archiet_microcodegen_java/` |
| `go-v*.*.*` | `archiet-microcodegen-go` | pkg.go.dev | `archiet_microcodegen_go/` |
| `laravel-v*.*.*` | `archiet-microcodegen-laravel` | Packagist | `archiet_microcodegen_laravel/` |

Plus this repo's own `microcodegen` package, built from `microcodegen.py` at
the root by `.github/workflows/publish-pypi.yml`.

## Required secrets — nothing publishes until these exist

These lived on `aniekanasuquookono-web/archiet` and **must be recreated here**
(Settings → Secrets and variables → Actions). Until then every release workflow
fails at its credential check.

| Secret | Used by |
|---|---|
| `PYPI_API_TOKEN_MICROCODEGEN` | `pypi-publish.yml`, `pypi-publish-mcp.yml` |
| `PYPI_API_TOKEN` | `pypi-publish-django.yml`, `pypi-publish-flask.yml` |
| `NPM_TOKEN` | `npm-publish-nestjs.yml` |
| `CRATES_IO_TOKEN` | `crates-publish-tauri.yml` |
| `NUGET_API_KEY` | `nuget-publish-dotnet.yml` |
| `GEM_HOST_API_KEY` | `gem-publish-rails.yml` |
| `MICROCODEGEN_GO_PAT` | `go-publish.yml` — pushes to the Go mirror repo |
| `MICROCODEGEN_LARAVEL_PAT` + `PACKAGIST_API_TOKEN` | `packagist-publish-laravel.yml` |

`maven-release-java.yml` needs none — it attaches a fat JAR to a GitHub release.

## The two mirror repositories stay where they are

`go-publish.yml` and `packagist-publish-laravel.yml` **sync into** separate
repos rather than publishing from here, because Go module paths and Packagist
package names are bound to a repository URL:

- `aniekanasuquookono-web/archiet-microcodegen-go`
- `aniekanasuquookono-web/archiet-microcodegen-laravel`

Do not move or rename those. Changing the Go module path breaks `go install`
for anyone who already has it. They are publish targets, not sources — the
source of truth is `archiet_microcodegen_go/` and `archiet_microcodegen_laravel/`
in this repository.

## Two Python packages, deliberately

| Package | Built from | Generates | Version |
|---|---|---|---|
| `microcodegen` | `microcodegen.py` (root) | **Flask** | 0.1.0 |
| `archiet-microcodegen` | `scripts/microcodegen.py` | **FastAPI** | 0.2.3 |

Both are live on PyPI. They diverged while the code lived in two repositories:
the root file is 1,122 lines and emits Flask; `scripts/` is 1,396 lines, emits
FastAPI, and tolerates numbered PRD section headings the older one rejects.

They are kept as separate packages on purpose. Collapsing them would either
change what `pip install microcodegen` produces — Flask today, FastAPI after —
or force a version regression on `archiet-microcodegen` from 0.2.3 back to
0.1.0. Neither is worth doing to existing users for tidiness.

Reconciling the two engines is a real piece of work, not a merge. Do it as its
own change, with a major version bump and a migration note, when someone
actually wants it.

## Before the first release from this repository

1. Recreate the secrets above.
2. Run one workflow with `dry_run: true` — several support it — and confirm the
   artifact builds before trusting the rest.
3. Only then archive `aniekanasuquookono-web/archiet`.

Archiving before step 2 freezes four live PyPI packages, an npm package and the
rest at their current versions with no way to ship a fix.
