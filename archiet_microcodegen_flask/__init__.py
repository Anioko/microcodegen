"""archiet-microcodegen-flask — PRD text → Flask app ZIP, pure stdlib, zero LLM calls.

Flask variant of the Archiet microcodegen algorithm.

Public API:

    from archiet_microcodegen_flask import microcodegen_flask, parse_prd
    from archiet_microcodegen_flask import manifest_to_genome, render_genome, pack

    # or use the alias:
    from archiet_microcodegen_flask import microcodegen

CLI:

    archiet-microcodegen-flask path/to/prd.md > app.zip
    archiet-microcodegen-flask path/to/prd.md --out ./out/

Constraints:
  - Pure stdlib; zero app.* / agents.* / templates/ imports
  - Hard ceiling: <1400 LOC for the core algorithm (_core.py)
  - No LLM calls; deterministic regex extraction only
"""

from __future__ import annotations

from archiet_microcodegen_flask._core import (
    main,
    manifest_to_genome,
    microcodegen,
    microcodegen_flask,
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
    "microcodegen_flask",
    "pack",
    "parse_prd",
    "render_genome",
]
