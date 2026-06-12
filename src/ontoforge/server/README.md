# ontoforge.server — the REST API + SPA over a project directory

`create_app(project)` builds a FastAPI app over one project directory (the
same artifacts the CLI writes); `run_server(...)` is the `ontoforge serve`
entry. Every endpoint is `async` on purpose so FastAPI keeps them on the
single event-loop thread — the project's sqlite connection is thread-affine —
and `ProjectWorld.lock` (one RLock) serializes all ledger/hearth access.

## Modules

| file         | role |
|--------------|------|
| `app.py`     | endpoint wiring, request/response mapping, the SPA mount |
| `world.py`   | `ProjectWorld`: lazily-opened ledger/hearth/ontology/engine, the answer cache, question recording, workspace + export helpers |
| `search.py`  | federated search: scoring tiers, the lazy entity value index, per-kind searchers |
| `schemas.py` | pydantic request/response models |
| `static/`    | the SPA (owned by the UI crew) |

## API surface

Core (M12/M13): `/api/status`, `/api/reload`, `/api/ontology[...]`,
`/api/ask`, `/api/ask/clarify`, `/api/entities/{uri}`, `/api/atoms/{id}`,
`/api/provenance/{ref}`, `/api/review[...]`, `/api/dashboards`.

OS shell additions:

- `GET /api/search?q=<query>&limit=20` — the frozen federated-search contract:
  `{"results":[{"kind":"class|entity|property|question|app","title","subtitle",
  "ref","score"}]}`. Ranked exact-prefix > word-prefix > substring > fuzzy in
  **disjoint score bands** so kinds interleave purely by score. Refs by kind:
  class uri / entity uri / `class_uri#prop` / question text / app id.
  The entity value index is lazy, in-memory, memory-capped (only functional
  key props + name/title/id/number/code/key-patterned props + entity uris from
  system-open HEARTH cells) and is dropped by `POST /api/reload`.
- `GET|PUT /api/workspace` — an arbitrary JSON window-layout blob persisted
  atomically (tmp + rename) at `<project>/workspace.json`.
- `POST /api/export {out_dir?}` — runs `amber.snapshot` into
  `<project>/exports/<n>/` (or a caller-named dir under the project) and
  returns `{bundle_dir, manifest_path, files, total_bytes}`;
  `GET /api/exports` lists past bundles. Bundles pass `amber.verify`.
- `GET /api/entities/{uri}/neighbors` — current-stance link neighborhood:
  `{links:[{predicate, direction: "out"|"in", target_uri, target_label}]}`
  (registered before the greedy entity-card route; entity uris carry slashes).

Every `/api/ask` records the question text as a ledger artifact of kind
`question` (constraint-H provenance over a minted question atom, idempotent
per text), which is what search `kind=question` reads — asked questions
survive server restarts.

## Performance (aviation demo world, 150-row slice)

Measured by `tests/server/test_perf.py` (asserted loosely at <1s for CI;
the prints carry the real numbers): `/api/search` p95 ≈ 104 ms (target
<150 ms), `/api/ask` cache hit p95 ≈ 2 ms (target <50 ms).

## Tests

`tests/server/` — zero network: `TestClient` drives the ASGI app in-process
over a real materialized project fixture. Note the thread-affinity rule when
writing tests: never open the session world's ledger from the test thread;
introspect HEARTH through a private `Hearth` + second `SqliteLedger`
connection instead (see `test_search.py` / `test_neighbors.py`).
