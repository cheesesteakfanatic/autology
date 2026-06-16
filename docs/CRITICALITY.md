# Criticality — lazy, usage-driven recompute over the ontology graph (§6)

OntoForge tracks how *critical* each element of an induced ontology is — which
classes the platform actually leans on — and keeps that ranking fresh **lazily**,
recomputing only what recent usage could have changed. Criticality is the signal
that tells an autonomous data OS where to spend attention: which types to keep
materialized, which joins to revalidate first, which corners of the schema are
load-bearing versus dead weight.

It is a self-contained engine module (`src/ontoforge/criticality/`) wired into the
product through the **backend only** — a read-only API endpoint, additive usage
emission from the existing handlers, and a CLI command. No new UI.

## What criticality is

Every element (an ontology **class**, the node of the criticality graph) gets a
score in `[0, 1]` that blends four normalized, deterministic signals:

| signal              | weight | meaning |
|---------------------|:-----:|---------|
| **usage frequency** | 0.40  | how often the element is touched, relative to the busiest element |
| **centrality**      | 0.25  | graph degree relative to the most-connected element |
| **recency**         | 0.20  | exponential decay over the integer usage `seq` (half-life 50 seqs) |
| **dependents**      | 0.15  | how many elements depend on it, relative to the most-depended-on |

```
score = 0.40 * usage_freq_norm
      + 0.25 * centrality_norm
      + 0.20 * recency
      + 0.15 * dependents_norm
```

The weights sum to exactly `1.0` and that invariant is asserted at import time
(`src/ontoforge/criticality/recompute.py`). Recency uses the post-update
watermark (the highest folded `seq`) as "now": an element last touched
`HALF_LIFE` seqs ago contributes recency `0.5`. Every division is guarded against
zero, so an empty or single-node graph never raises.

## The lazy dirty-set / watermark recompute

The model never re-scores the whole graph. It is driven by an **append-only
usage log** whose events each carry a strictly increasing integer `seq` assigned
by the log itself (there is no wall-clock — see *Keyless & deterministic*).

On each `update(log)`:

1. it reads only the **unseen tail** of the log — `log.since(watermark)`;
2. for every new event it accumulates weighted usage on the touched element and
   marks that element **plus its adjacency neighbors** *dirty* (a neighbor's
   usage shifts the centrality-weighted blend);
3. it re-scores **only the dirty set** — not the whole ontology;
4. it advances the **watermark** to `log.max_seq`.

Because the watermark advances on every fold, re-running `update` with the same
log is a clean no-op (recomputes nothing). This is what makes **incremental
recompute identical to a batch recompute** and keeps cost proportional to recent
activity, not to ontology size — the whole point of a *lazy* criticality layer.

## How query / join / materialize usage feeds it (the backend seam)

The criticality graph is **induced from the active world** exactly as the
`/api/ontology` and `/api/atlas` handlers report it (`src/ontoforge/server/usage.py`):

- **nodes** = ontology class URIs;
- **edges** = the typed relationships — the ontology's link properties
  (`source --link--> range_class`) UNIONED with the persisted atlas arcs
  (`src_class <-> dst_class`), treated as an undirected adjacency graph;
- **dependents** = the reverse direction of each link/arc.

The graph is cached per active world (keyed by project path + world name) and
re-induced automatically on a world switch or an in-place ontology edit (a
playground build or an engineer apply drops the cache).

Usage is emitted **additively** from handlers that already exist — without
changing any response contract:

- **`query`** — `world.ask` (`POST /api/ask`) records a `query` event for every
  class URI the answer's OQIR plan touched, derived defensively from
  `Answer.oqir` (the `Select` nodes the plan grounded against). A failed/abstained
  answer with no plan simply records nothing.
- **`join`** — when a *confirmed* typed relationship is applied
  (`POST /api/engineer/apply` of an `AddProperty` carrying a `range_class`),
  `world.engineer_apply` records a `join` event for the relationship's two
  endpoint class URIs, after the new ontology is persisted (so the endpoints are
  known nodes of the re-induced graph).
- **`materialize`** / **`answer`** are also recognized usage kinds (the log
  rejects anything else), available for future emitters.

All emission is a pure side effect wrapped in defensive `try/except`: it can
never alter a response, change a status code, or turn a working endpoint into a
500. A world with no ontology yet contributes an empty graph and the model stays
empty.

## Keyless & deterministic

Criticality holds the same hard invariants as the rest of the engine:

- **keyless** — no API key at import or at call;
- **offline** — pure in-process stdlib; zero network;
- **fully deterministic** — there is **no wall-clock** anywhere. The only notion
  of "time" is the integer `seq` the usage log assigns (1, 2, 3, …), so a given
  sequence of usage events always reproduces byte-identical scores and a
  byte-stable JSON snapshot (`save_scores`). Replaying the same asks yields the
  same ranking.

## The `/api/criticality` endpoint

```
GET /api/criticality?top=N        (default N = 10)
```

Read-only. Returns the top-`N` most critical elements of the active world,
score-descending:

```json
{
  "elements": [
    {"uri": "onto://class/…", "label": "Site", "score": 0.6364, "kind": "class"},
    {"uri": "onto://class/…", "label": "SupportTicket", "score": 0.3045, "kind": "class"}
  ],
  "total": 2
}
```

`label` is the ontology class name (falling back to an atlas component label,
then the URI tail). `total` is how many elements currently carry a non-default
score. An **unbuilt world** (no ontology yet) returns `{"elements": [], "total": 0}`
with HTTP 200 — never an error. Repeated GETs on a stable usage log are
byte-identical.

## The CLI command

```
ontoforge criticality -p PROJECT [--top N]     (alias -n N; default 10)
```

Loads the active world, builds the criticality graph from the answering ontology
+ the connection atlas, **replays any recorded usage** (the ledger's saved
`question` artifacts, re-answered through LODESTONE and folded as `query`
events), and prints the score-ranked elements in a clean table:

```
         criticality (top 2, replayed 1 usage events)
 #   element         uri                              score
 1   Site            onto://class/f108e840fcc24222   0.6364
 2   SupportTicket   onto://class/a2f5c0115067022b   0.3045
```

A materialized project with **no recorded usage** prints an honest
"no critical elements yet" message and still exits 0 — criticality is
usage-driven and lazy, so it reports nothing until the world has been used.

## Where it lives

| concern | file |
|---------|------|
| usage log (append-only, integer seqs) | `src/ontoforge/criticality/usage.py` |
| lazy model (dirty-set / watermark)     | `src/ontoforge/criticality/recompute.py` |
| byte-stable JSON snapshot              | `src/ontoforge/criticality/store.py` |
| backend bridge (graph + emit + read)   | `src/ontoforge/server/usage.py` |
| `GET /api/criticality`                 | `src/ontoforge/server/app.py` |
| usage emission seam                    | `src/ontoforge/server/world.py` (`_record_query_usage`, `_record_join_usage`) |
| `ontoforge criticality`                | `src/ontoforge/cli.py` |
| tests                                  | `tests/criticality/`, `tests/server/test_criticality_api.py`, `tests/cli/test_criticality_cmd.py` |
