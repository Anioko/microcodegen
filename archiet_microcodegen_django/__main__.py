"""Allow `python -m archiet_microcodegen_django` invocation."""

from __future__ import annotations

import sys

from archiet_microcodegen_django import main


if __name__ == "__main__":
    sys.exit(main())
