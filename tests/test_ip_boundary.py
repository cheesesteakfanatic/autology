"""IP-boundary import guard (v2.1 §18–20) — see docs/IP_ARCHITECTURE.md.

A pragmatic tripwire (NOT a full enforcement engine) on the one import direction
that matters for a clean closed-core / open-shell repo split: the OPEN SHELL
(``server``, ``cdc``) must not reach into a NEW closed-core engine *internal*
submodule. It may consume the engine only via the published package entrypoint
(``from ontoforge.relationships import …``) or the shared ``contracts``.

AST-based, deterministic, zero-network. What it checks and what it deliberately
does not check is documented in docs/IP_ARCHITECTURE.md.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import ontoforge

SRC = Path(os.path.dirname(ontoforge.__file__))

#: the NEW closed-core engine packages whose INTERNALS the open shell must reach
#: only via the package entrypoint (Wave-1 inventions).
NEW_CLOSED_CORE = {
    "relationships",
    "validation",
    "ensemble",
    "tenant",
    "discovery",
}

#: the open-shell packages this guard scans.
OPEN_SHELL = ["server", "cdc"]

#: documented carve-out (docs/IP_ARCHITECTURE.md §3): pre-existing internal
#: imports of ESTABLISHED closed-core modules that predate this boundary. These
#: are honest debt, recorded explicitly — not silently allowed. The guard only
#: governs the NEW engine packages, so these established ones are simply not in
#: scope; this set documents the intent for a future cleanup / repo split.
KNOWN_ESTABLISHED_INTERNAL_IMPORTS = {
    "ontoforge.vista._pipeline",
    "ontoforge.lodestone.model",
    "ontoforge.lodestone.execute",
}


def _module_targets(path: Path) -> list[str]:
    """Every dotted import target in a python file (from-imports and plain
    imports), as written."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — stays inside the same package
                continue
            if node.module:
                targets.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
    return targets


def _py_files(pkg: str) -> list[Path]:
    root = SRC / pkg
    return sorted(root.rglob("*.py")) if root.is_dir() else []


def test_open_shell_does_not_import_new_closed_core_internals() -> None:
    """``server`` / ``cdc`` may import a new engine package's ENTRYPOINT
    (``ontoforge.relationships``) but never a SUBMODULE
    (``ontoforge.relationships.signals``)."""
    violations: list[str] = []
    for pkg in OPEN_SHELL:
        for f in _py_files(pkg):
            for target in _module_targets(f):
                parts = target.split(".")
                if len(parts) < 3:
                    continue  # ontoforge.<pkg> is the entrypoint — allowed
                if parts[0] != "ontoforge":
                    continue
                engine = parts[1]
                if engine in NEW_CLOSED_CORE:
                    rel = f.relative_to(SRC)
                    violations.append(f"{rel}: imports internal '{target}' "
                                      f"(use 'from ontoforge.{engine} import ...')")
    assert not violations, (
        "open-shell modules import NEW closed-core engine internals "
        "(see docs/IP_ARCHITECTURE.md):\n  " + "\n  ".join(violations)
    )


def test_contracts_is_the_sanctioned_cross_boundary_channel() -> None:
    """``contracts`` is always an allowed cross-boundary import — it is the
    shared typed-interface surface both rings depend on. (Sanity: the open shell
    does in fact consume the engine THROUGH contracts.)"""
    uses_contracts = False
    for pkg in OPEN_SHELL:
        for f in _py_files(pkg):
            if any(t.startswith("ontoforge.contracts") for t in _module_targets(f)):
                uses_contracts = True
    assert uses_contracts, "expected the open shell to consume contracts"


def test_carveout_list_only_references_established_modules() -> None:
    """The documented carve-out must never silently cover a NEW engine package —
    if it ever did, the boundary would be meaningless for the inventions we are
    actually protecting."""
    for entry in KNOWN_ESTABLISHED_INTERNAL_IMPORTS:
        engine = entry.split(".")[1]
        assert engine not in NEW_CLOSED_CORE, (
            f"carve-out '{entry}' covers a NEW closed-core package — not allowed"
        )


def test_new_closed_core_packages_declare_their_ip_status() -> None:
    """Every NEW closed-core package's __init__ must carry the CLOSED-CORE IP
    banner (§18), so the boundary is self-documenting at the source."""
    missing: list[str] = []
    for engine in sorted(NEW_CLOSED_CORE):
        init = SRC / engine / "__init__.py"
        if not init.is_file():
            missing.append(f"{engine}/__init__.py missing")
            continue
        text = init.read_text(encoding="utf-8")
        if "CLOSED-CORE IP" not in text:
            missing.append(f"{engine}/__init__.py lacks the CLOSED-CORE IP banner")
    assert not missing, "closed-core IP banner missing:\n  " + "\n  ".join(missing)
