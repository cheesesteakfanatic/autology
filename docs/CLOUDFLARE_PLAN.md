# OntoForge on Cloudflare — deployment architecture plan

Status: **plan only** (deploy is "down the line"). Grounded against the Cloudflare platform skill +
2026 product state. The thing that shapes everything: OntoForge's engine is **heavy Python**
(DuckDB, pyarrow/Parquet, pandas, scikit-learn, rdflib; TANE FD discovery, FCA lattices). That does
**not** run on Workers (JS/Wasm; Python Workers can't load these C-extension/native deps). So the
engine needs a real container or host; only the SPA and the LLM layer are natively Cloudflare.

## The honest constraint table

| Piece | Runs on Workers? | Where it goes |
|---|---|---|
| Vanilla-JS no-build SPA (`server/static/`) | n/a (static) | **Cloudflare Pages** — drops in directly |
| FastAPI + DuckDB + sklearn engine | ❌ (native deps) | **Cloudflare Containers** (beta) *or* external container host |
| Materialized Parquet worlds + AMBER bundles | — | **R2** (S3-compatible; our export already writes Parquet) |
| SQLite provenance ledger | — | container disk+R2 sync (interim) → **D1** (clean, later) |
| LLM "experts" for the ensemble gate | ✅ | **Workers AI** (cheap open models) + **AI Gateway** |
| Provider API keys (Kimi/Opus) | ✅ | **Secrets Store** on the Worker — never in the engine |

## Recommended topology — phased

### Phase 1 (ship fast, low risk): Pages + external engine + AI Gateway
- **SPA → Cloudflare Pages.** Our app is already no-build static ES modules; point Pages at
  `server/static/`. Global CDN, custom domain, the no-cache headers we already enforce.
- **Engine → a plain container host** (Fly.io / Render / Railway / a small VM) running the existing
  `ontoforge serve` image, fronted by Cloudflare (proxied DNS or a **Tunnel** so no public IP).
  This sidesteps the Containers beta while we validate.
- **LLM layer → AI Gateway now.** Even in Phase 1, the gate's live experts call models through
  **AI Gateway's OpenAI-compatible endpoint**
  (`https://gateway.ai.cloudflare.com/v1/{account}/{gateway}/compat`), which gives **caching +
  rate-limiting + retry/fallback + observability for free** — exactly our cost controls. Our
  `aimodels/router.py` just registers an OpenAI-compatible `ModelSpec` pointed at that base URL.

### Phase 2 (all-Cloudflare, when Containers is GA): collapse onto one platform
- **Engine → Cloudflare Containers.** A thin Worker front + a **container-as-Durable-Object** running
  the Python image. The DO model is a *gift* for us: `getByName(tenantId)` gives **per-tenant
  container isolation** — which is exactly the whitepaper's single-tenant-data-plane requirement,
  for free. Caveats to design around: cold start ~2–3s, **ephemeral disk** (persist worlds to R2,
  ledger to D1), manual load-balancing (no autoscale), beta API churn.
- **Worlds → R2.** `hearth.export_canonical` / AMBER already emit Parquet; write/read them from R2
  (S3 API). The active-world switch becomes an R2 prefix swap.
- **Ledger → D1.** Migrate the SQLite ledger to D1 (HTTP SQLite) for durability across ephemeral
  container restarts. Interim: keep file-SQLite on disk and snapshot to R2 on each commit.

## The LLM layer maps onto what we already built (zero engine rework)
The keyless `ModelClient` seam + `aimodels/router.py` + the `ensemble/` gate were designed for this:
- Register a **Workers AI** model as the cheap "expert" tier (per-neuron pricing; e.g. Llama-3.1-8B,
  Mistral-7B, a Qwen/DeepSeek-coder model — *verify the current Workers AI catalog for the exact
  Qwen id at deploy time*). Register **Kimi K2** (Moonshot) and **Claude Opus** as frontier-tier
  `ModelSpec`s routed through AI Gateway (BYOK, OpenAI-compatible).
- The gate's economics get cheap by construction: **deterministic experts vote first; model experts
  fire only on genuinely ambiguous DE actions** (the cascade), and **AI Gateway caches** identical
  vote contexts → most decisions cost $0. This is the FrugalGPT/cascade economics the whitepaper
  wanted, now with a concrete cheap backend.
- **Keyless invariant preserved:** the engine never holds a provider key — it calls AI Gateway with a
  gateway token (Worker secret); if the gateway is unreachable, the router falls back to the
  deterministic tier and the app keeps working. The no-key demo path is unchanged.

## Secure-data posture on Cloudflare
- The `aimodels/secure.py` layer (PII redaction, stratified sampling-not-bulk, untrusted-text
  spotlighting, injection scan) runs **before** any AI Gateway call — sensitive estates can also pin
  the gate to **Workers AI** (data stays on Cloudflare's network) or to the deterministic tier
  (data never leaves the container). AI Gateway DLP/guardrails add a second layer.

## Rough config shape (Phase 2 sketch — not yet wired)
```jsonc
// wrangler.jsonc (the Worker that fronts the container + SPA)
{
  "name": "ontoforge",
  "compatibility_date": "2026-06-01",
  "containers": [{ "class_name": "EngineContainer", "image": "./Dockerfile", "instance_type": "standard" }],
  "durable_objects": { "bindings": [{ "name": "ENGINE", "class_name": "EngineContainer" }] },
  "r2_buckets": [{ "binding": "WORLDS", "bucket_name": "ontoforge-worlds" }],
  "d1_databases": [{ "binding": "LEDGER", "database_name": "ontoforge-ledger" }],
  "ai": { "binding": "AI" },                       // Workers AI
  "assets": { "directory": "./src/ontoforge/server/static" }  // SPA
}
```
The engine container reads `AI_GATEWAY_URL` + `AI_GATEWAY_TOKEN` (Worker secrets) and registers them
as router `ModelSpec`s; nothing else in the Python codebase changes.

## Open questions to resolve at deploy time (verify against live docs)
1. Containers GA status + max instance size (our FCA/TANE peaks need RAM headroom).
2. Exact Workers AI model ids for Qwen-class + their neuron pricing.
3. D1 size/row limits vs our ledger growth (atoms can be large) — D1 vs Hyperdrive(Postgres) vs
   container-volume+R2.
4. R2 read latency for cold worlds vs keeping a hot world in the container.
