"""Static-asset integrity guards for the SPA module graph.

The UI is a vanilla ES-module graph with no build step, so a renamed or removed
module is only caught at runtime in a browser — every file still returns 200,
but the entry graph fails to link and the app boots to a blank screen. These
tests catch that class of break at CI time by (a) resolving every relative
`import ... from "./x.js"` to a file on disk, and (b) asserting the server
sends no-cache on static assets so a browser never serves a stale graph.
"""

from __future__ import annotations

import re
from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "ontoforge" / "server" / "static"

_IMPORT_RE = re.compile(r"""(?:import|export)\b[^'"]*?from\s+['"](\.[^'"]+)['"]""")
_BARE_IMPORT_RE = re.compile(r"""import\s+['"](\.[^'"]+)['"]""")


def _js_files() -> list[Path]:
    return [p for p in STATIC_DIR.rglob("*.js") if "vendor" not in p.parts]


def test_every_relative_import_resolves_to_a_file():
    """Every `from "./x.js"` (and bare `import "./x.js"`) points at a real file.

    A miss here is exactly the renamed-module break that blanks the live app
    while every individual file still 200s."""
    missing: list[str] = []
    for js in _js_files():
        src = js.read_text(encoding="utf-8")
        specs = set(_IMPORT_RE.findall(src)) | set(_BARE_IMPORT_RE.findall(src))
        for spec in specs:
            target = (js.parent / spec).resolve()
            if not target.is_file():
                missing.append(f"{js.relative_to(STATIC_DIR)} imports {spec} -> missing")
    assert not missing, "broken relative imports in the SPA module graph:\n" + "\n".join(missing)


def test_app_entry_imports_all_resolve():
    """The boot module's imports must all resolve — if any does not, the whole
    graph fails to link and nothing renders."""
    app = STATIC_DIR / "app.js"
    specs = set(_IMPORT_RE.findall(app.read_text(encoding="utf-8")))
    assert specs, "app.js should import its modules"
    for spec in specs:
        assert (app.parent / spec).resolve().is_file(), f"app.js imports missing module: {spec}"


def test_registry_imported_apps_export_their_factories():
    """Each app module registry imports must export the named factory the
    registry expects (a named-export mismatch also blanks the app)."""
    reg = (STATIC_DIR / "js" / "apps" / "registry.js").read_text(encoding="utf-8")
    pairs = re.findall(r'import\s+\{\s*([A-Za-z0-9_]+)\s*\}\s+from\s+["\'](\.[^"\']+)["\']', reg)
    assert pairs, "registry.js should import app factories"
    for name, spec in pairs:
        mod = (STATIC_DIR / "js" / "apps" / spec).resolve()
        assert mod.is_file(), f"registry imports missing module {spec}"
        body = mod.read_text(encoding="utf-8")
        assert re.search(rf"export\s+(?:function|const)\s+{re.escape(name)}\b", body), (
            f"{spec} does not export {name} (registry link would fail)"
        )


def test_static_assets_are_no_cache(client):
    """Static assets must forbid caching so a fresh build never collides with a
    browser-cached stale module graph (the blank-app failure mode)."""
    for path in ("/static/app.js", "/static/js/core.js", "/static/style.css"):
        r = client.get(path)
        assert r.status_code == 200, path
        cc = r.headers.get("cache-control", "")
        assert "no-cache" in cc or "no-store" in cc, f"{path} must be no-cache, got {cc!r}"
    root = client.get("/")
    assert "no-cache" in root.headers.get("cache-control", ""), "index must be no-cache"
