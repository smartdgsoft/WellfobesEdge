#!/usr/bin/env python3
"""Boundary guard — enforces the loose coupling that makes the edge deployable
standalone and the monorepo splittable later.

Rules:
  * edge/   may import   shared/   — never center/
  * center/ may import   shared/   — never edge/
  * shared/ may import   NEITHER edge/ nor center/   (it's the neutral contract)

Run in CI and locally (`make check`). A violation exits non-zero with the exact
file and import, so the coupling is caught the moment it's introduced — not
during a customer's standalone deploy or a repo split.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# module prefixes owned by each zone
ZONES = {
    "edge": {"forbidden": ("center", "historian"), "self": ("gateway",)},
    "center": {"forbidden": ("edge", "gateway"), "self": ("historian",)},
    "shared": {"forbidden": ("edge", "center", "gateway", "historian"), "self": ("wellfobes_contract",)},
}


def imported_names(path: pathlib.Path):
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as e:
        print(f"  ! could not parse {path}: {e}")
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node.lineno, a.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.lineno, node.module


def check() -> int:
    violations = []
    for zone, rules in ZONES.items():
        base = ROOT / zone
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            for lineno, mod in imported_names(py):
                top = mod.split(".")[0]
                if top in rules["forbidden"]:
                    rel = py.relative_to(ROOT)
                    violations.append((zone, rel, lineno, mod))

    if violations:
        print("✗ BOUNDARY VIOLATIONS — the zones must stay loosely coupled:\n")
        for zone, rel, lineno, mod in violations:
            print(f"  {rel}:{lineno}  ({zone}/ imports '{mod}')")
        print("\n  edge & center may only share via 'wellfobes_contract' (shared/).")
        print("  Fix the import before this ships — it breaks standalone deploy / repo split.")
        return 1

    print("✓ boundaries clean — edge, center, and shared are loosely coupled")
    print("  edge imports only shared; center imports only shared; shared imports neither.")
    return 0


if __name__ == "__main__":
    sys.exit(check())
