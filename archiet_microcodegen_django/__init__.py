"""archiet-microcodegen-django — PRD text → Django REST Framework app ZIP.

Pure stdlib. Zero dependencies. Zero LLM calls.

Public API:
    from archiet_microcodegen_django import microcodegen_django
    from archiet_microcodegen_django import microcodegen  # alias
    from archiet_microcodegen_django import parse_prd, manifest_to_genome
    from archiet_microcodegen_django import render_genome, pack, main

CLI:
    archiet-microcodegen-django path/to/prd.md > app.zip
    archiet-microcodegen-django path/to/prd.md --out ./out/
"""

from __future__ import annotations

from archiet_microcodegen_django._core import (
    main,
    manifest_to_genome,
    microcodegen,
    microcodegen_django,
    pack,
    parse_prd,
    render_genome,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "main",
    "manifest_to_genome",
    "microcodegen",
    "microcodegen_django",
    "pack",
    "parse_prd",
    "render_genome",
]
