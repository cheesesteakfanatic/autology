# R0 Findings — UI maturation · performance · missing features & company agents

Synthesis of three research briefs against the live codebase (commit 652916d; 25 modules).
Grounded in the actual files: `server/static/style.css` (79,480 B), `js/constellation.js`,
`js/apps/datamap.js`, `profiling/fds.py`, `profiling/inds.py`, `profiling/sketches.py`,
`strata/lattice.py`. Drives the next autonomous waves (W1.5 → W5).

**Hard constraint already breached:** non-vendor payload is **287,370 B** — 650 B *over* the
280 KB (286,720 B) ceiling **today**. W2-UI must NET-SHRINK CSS/JS before adding any byte.
This is a gating fact, not a footnote: every UI change below is scored partly on bytes.

Two-layer labeling honored throughout: **verifiable facts** (incumbent capabilities, code
state, research thresholds) are separated from **judgment** (positioning, sequencing calls).
Strategy/positioning is judgment, deferred to the MEMO wave.

---

## 1. UI MATURATION — warm-but-grown-up (drives **W2-UI**)

North star (replace the CSS header's "molded-fiberglass / Charley-Harper / atomic-age" framing):
**warm, neutral-dominant, editorial. Color is information, not decoration. One accent per view.
Serif for gravitas, mono for data, neutral sans for chrome.** The current header optimizes for
theme-fidelity over restraint — that framing is *what produces the childish output*. Reframe it
first; it governs every future token decision.

Ranked by impact-per-byte (each is byte-neutral-or-negative; the rainbow-collapse and motif
removals BUY the headroom the rest spends):

| # | Change | Before → After | Why it reads grown-up | Byte impact |
|---|--------|----------------|------------------------|-------------|
| **U1** | **Chroma discipline** — pull the 8-hue wheel + marigold from ~50-70% S into a 25-40% band | `--marigold #E0A126→#C68A2E` · `--teal #1F6F6B→#2D6460` · `--terracotta #C75B39→#B0603F` · `--persimmon #B8532A→#A85A3B` · `--avocado #7C8A3B→#79803F` · `--ocean #2D6E8E→#3A6378` · `--plum #6E4A63→#6A5060` · `--dusty-rose #C98B7A→#C0928A` · `--mustard` keep | Low saturation = perceived heritage/status; perceptual luxury threshold ~20-30% S (J. Consumer Research 2025). ~50-70% S is the literal "crayon" tell | ~0 (value swaps) |
| **U2** | **Neutral : accent ratio** — collapse the rainbow to TWO semantic accents | `.titlebar` colored-hue fill → neutral `--bisque/--cream` + 2px accent underline; collapse 5 per-card 3px left-edge hues (`.build-export` persimmon, `.build-extract` avocado, `.console-card.clarify` ocean, `.dash-rank` avocado, `.stance .scrub-stance` ocean) → hairline neutral default + **teal=confirmed/cited, marigold=the one primary action**; keep 8-hue wheel ONLY as information (atlas islands, category chips, cite-dots) | Stripe/Linear/Vercel = mostly neutrals + ONE measured accent; color ≤5-10% of pixels. Today nearly every component touches a hue | **NET NEGATIVE** (deletes rules) |
| **U3** | **Real serif for headlines + demote small-caps** | add `--serif: 'Georgia','Iowan Old Style',ui-serif,serif` (system stack — honors offline invariant, zero network) used ONLY for hero display (`.answer-headline`, `.clarify-q`, `.notready-title`, `class-detail h2`); swap geometric `--sans` Futura→humanist `'Inter',-apple-system,'Segoe UI',system-ui`; **remove `font-variant:small-caps`** from buttons/mode-seg/dock/win-title/domain-name (29 uses today), reserve only for tiny eyebrow kickers | Futura-first + small-caps everywhere = retro-poster costume, not editorial. A serif headline carries quiet authority; mono stays for data | ~0 (token + targeted removals) |
| **U4** | **Reduce radii + kill toy motifs** | `--radius 12→8px`, `--radius-win 14→10px`, dock `18→12px`; `999px` pills → `6px` rounded-rects; **delete** `@keyframes dot-pulse` (scale(2) bounce), `.wm-spark` atomic starburst, conic-gradient `.di-dot`, Charley-Harper coin traffic-lights, 45° striped `.chart-placeholder` | >20px radius + pills read toy/candy; 8-12px reads pro. Illustrative motifs are the literal "childish" content | **NET NEGATIVE** (deletes keyframes+gradients) |
| **U5** | **Calm the motion — confident not bouncy** | remove `@keyframes dot-pulse`, `switcher-halo` (coach-lit ring), `likely-breathe` pulse (use static lower-opacity dashed stroke), `node-pop` overshoot; soften `value-flash` to one-shot bg fade; keep base `cubic-bezier(0.2,0.8,0.2,1)`, all entrances pure ease-out 140-180ms, no overshoot | Spring/bounce is the #1 "playful/informal" tell. Keep SHORT ease-out so it still feels interaction-dense (itself a premium signal) | **NET NEGATIVE** |
| **U6** | **Quieter grain + shadows** | grain `baseFrequency 0.9→0.65`, `opacity 0.03→0.025`; `--shadow-2 0 6px 18px/.16 → 0 4px 14px/.12`; `--shadow-3 0 16px 48px/.22 → 0 12px 36px/.18`; **remove** the `rgba(255,253,247,0.6) inset` plasticky highlight (keep ≤0.3 inset) | High-freq grain sparkles on retina; bright inset = skeuomorphic plastic. KEEP the warm-amber shadow (differentiator vs gray SaaS) — just smaller | ~0 |
| **U7** | **Editorial type scale/weights/tracking** | cap body/label tracking at `0.01em` (eyebrow keeps ~0.08em, down from 0.14em); exactly two weights (400 body, 600 emphasis) — drop 700s on `.seg-badge/.rail-badge/.abstain-mark::before` to 600; `--fs-5 1.75→2rem`, add `--fs-6 2.75rem` hero; body `line-height 1.6→1.55` | Wide tracking + free 500/600/700 jumps = un-restrained. Premium = disciplined scale, denser pro text | ~0 |
| **U8** | **Data density for pros** | add `--space-tight: 0.375rem`; `table.data th/td 0.5rem 1rem → 0.375rem 0.75rem`; `.answer-card/.review-card/.stance-card 1.5rem → 1.125rem`; keep airy whitespace ONLY on the Ask hero | CDOs/data engineers expect density, not onboarding air. Tighten data surfaces ~15-20% | ~0 |

**Pitfalls (test-enforced — do NOT trip these):**
- Don't desaturate to gray — pull S down ~30-40%, NOT to zero. Founder LIKES the warmth; target
  is muted-warm editorial paper, not a Linear clone.
- No webfonts — serif MUST be a system stack (offline invariant is test-enforced).
- **Re-measure WCAG after every hue/L change.** `.btn-forge` uses `--ink` on `--marigold`;
  pills/badges use hue-text on tinted grounds. Keep every text use ≥4.5:1; recompute the
  documented ratios (espresso/cream 14.7:1, walnut 6.0:1, ink-faint 4.6:1, chrome 9.84/4.53).
- `tests/server/test_spa.py` greps tokens + the innerHTML ban. Adapt token-presence assertions
  to new behavior; **keep every security/gate assertion intact**.
- Dark theme (`data-theme=dark`) is the ONLY legit `rgba(0,0,0,…)` shadow home — update both
  `:root` and the dark override when restructuring shadow tokens.
- The 8-hue `ATLAS_HUES` order in `js/core.js` is a LOCKED contract — mute each hue, never
  remove categorical color from atlas/chips/cite-dots. Only DECORATIVE color goes.

---

## 2. PERFORMANCE — ranked optimizations (drives **W1.5** engine + **W2-UI** client)

**MEASURE-FIRST guardrail (do this before any optimization):** stand up a 50k-200k-row
micro-benchmark over `estates/wild.py` + meridian timing `discover_fds`, `discover_inds`,
MinHash build, and constellation frame time. Optimize in PROFILED order. **Every numba/numpy
rewrite must produce byte-identical FD/IND/MinHash output** to the pure-Python reference —
determinism is a load-bearing gate. Pin against existing determinism tests; keep a pure-Python
fallback so a numba import/compile failure degrades gracefully (preserves the keyless guarantee).

### Engine hot paths (Python — W1.5)

| # | Hot path (verified) | Technique | Expected | Effort |
|---|---------------------|-----------|----------|--------|
| **P1** | `profiling/fds.py:90-96` `_violations` (`Counter` per equivalence class) + `partition_product:71-87` (probe dict bucketize), called per node per level — TANE refinement is the dominant cost | `pandas.factorize`/`np.unique(return_inverse)` → int32 codes ONCE; g3 via `np.bincount` per class, product via argsort+segment scan, under `@njit(cache=True, nogil=True)` | **20-100×** | M (kernel + determinism pin + fallback) |
| **P2** | `profiling/inds.py:127-148` `discover_inds` — O(cols²) `frozenset[int]` intersections, NO pre-filter | sorted `int64 np.array` per column, `np.intersect1d`/searchsorted for exact \|A∩B\|; **gate every pair with a cheap MinHash/HLL containment lower-bound first** so exact intersect runs only on survivors | **5-30×** (prefilter dominates at high col counts) | M |
| **P3** | `profiling/sketches.py:223-226` `MinHash.add` — calls `hash64` k=64× PER distinct value | universal-hashing permutation trick: hash value ONCE → h, lane i = min of `(a_i·h+b_i) mod prime`, a/b as int64 numpy vectors, one `np.minimum` over k lanes | **10-50×** (also speeds the P2 prefilter) | S-M |
| **P4** | `strata/lattice.py` `recompute_covers` — O(n²) `frozenset` subset tests over concept extents | represent extents as Python int **bitsets**; `c.extent ⊂ p.extent` → bitwise `&`; n² scan stays but each op is integer | **2-5×** | S (near-zero risk) |
| **P5** | per-table FD + per-pair IND fan-out currently uses `multiprocessing` (spawn+pickle cost) | run kernels under `@njit(nogil=True)` thread pool — skips process spawn/pickle | moderate (latency, not throughput) | S (after P1/P2 land) |
| **P6** | bulk column stats / value-distribution / backward-join FK+coverage VALIDATION | **DuckDB-pushdown** the relational-shaped metrics (matches Polars on groupby, lowest mem, spill-to-disk). Does NOT replace TANE/CbO | **5-30×** on those metrics | M |

**STOP line:** leave FCA lattice mostly as-is (already iceberg-pruned by σ; stability caps exact
enumeration at n=12) beyond the cheap P4 bitset win. Do NOT express TANE partition-refinement or
FCA CbO closure as Polars/DuckDB SQL — bespoke set-algebra, not relational ops; SQL loses both
speed and the exactness the gates depend on. P3's permutation trick changes the signature bytes
(still unbiased Jaccard) — update any test pinning literal signature values deliberately.

### Front-end hot paths (web — W2-UI)

| # | Hot path (verified) | Technique | Expected | Effort |
|---|---------------------|-----------|----------|--------|
| **F1** | `constellation.js` (23 `svgEl` sites) + `datamap.js` (9) build EVERY node/edge/label as a `createElementNS` element; SVG ceiling ~1-2k elements; founder targets 250+ nodes | render to ONE `<canvas>` redrawn on the existing rAF tick (`constellation.js:212-215`); keep the seeded JS force sim. Keep SVG path under ~300 elements for crisp text/CSS hover; add hit-testing + an accessible text layer | smooth **60fps to several-thousand** nodes; lower GC/payload | M |
| **F2** | large data tables (extract/review) | DOM **virtualization (windowing)** — render ~30 visible rows + overscan in a phantom-height scroller, rows built with `el()` (honors innerHTML ban) | handles 100k+ rows | S (~100 lines) |

**The honest WASM verdict.** WASM is the WRONG tool for the two things it's most reached for here:
- It does **NOTHING for DOM paint cost** (the real table/graph-render bottleneck), and its compute
  win evaporates if you cross the JS↔WASM boundary per node/row.
- **Graph:** Canvas + plain-JS force sim hits frame budget at 250-few-thousand nodes. WASM only
  pays off past **~5-10k nodes** AND only if the ENTIRE sim lives in WASM linear memory writing to
  a shared `Float32Array`. Not warranted at current scale.
- **Tables:** windowed DOM beats WASM; canvas-table only past ~1M cells.
- **If/when** you cross ~5-10k nodes (profile-proven, not blog-proven): **Rust → wasm-bindgen**
  (Figma/Adobe-grade), NOT AssemblyScript (slower/thinner) and NOT C→Emscripten (heavy/unsafe
  glue, payload bloat). A WASM blob would blow the 280 KB budget and the offline/zero-CDN CSP.

**"Rewrite the UI in C++"? — NO.** A browser app's cost is layout/paint/DOM + network, which
C++→WASM cannot touch (WASM has no DOM access; every op round-trips through JS). C++→Emscripten
bloats payload (you have NEGATIVE headroom — already 650 B over) and breaks the no-build/offline/CSP
posture. The right surgical alternative: **Canvas for the graph, DOM windowing for tables, and a
tiny lazily-loaded Rust→WASM module ONLY for a proven hot pure-compute kernel** (e.g. client-side
CSV/Parquet parse, or layout sim at large scale) — never the UI itself.

---

## 3. MISSING FEATURES & COMPANY AGENTS — prioritized backlog (tagged by wave)

Verified code gap: `src/ontoforge/cdc/` has only `tabular.py` (file CSV/Parquet) + `docs.py` —
**no live-source connector**; the server has **no auth/tenant/RBAC** (`tenant/priors.py` is
per-tenant LEARNING, not access control). The whole induce-pipeline is gated on file uploads.

**Sequencing judgment (not verified research):** the honest order is
**connectors → Plan mode → auth/multi-tenancy → observability → flywheel → lazy-recompute →
anonymization.** The current AUTONOMOUS_ROADMAP queues connectors at W4 *after* W2-UI/W3-COMPANY;
that risks polishing a product no customer can use. **Recommendation: pull connectors + auth
forward inside W4** (they are P0 there), and keep W2-UI/W3-COMPANY in parallel since they don't
block each other.

### Product features

| Pri | Feature | Wave | Why now / detail |
|-----|---------|------|------------------|
| **P0** | **Source connectors** (Postgres/MySQL/S3/Snowflake/CSV-at-scale) | **W4** | The gating gap — nothing else matters until a customer's data can flow. Open-shell ring behind the contracts seam: connectorx/SQLAlchemy (PG+MySQL), DuckDB httpfs (S3), Snowflake connector, chunked CSV. Commodity = cost of entry, not moat |
| **P0** | **Plan mode** — governed data-subset puller | **W4** | Onboarding + trust answer to "don't ship us your whole DB." Engine proposes a stratified subset around candidate keys/cardinality/distribution edges → pull only that → induce → offer to widen. Cheapest path to first-value (B2B activation only 37.5%; automated trials cut TTV 35%) |
| **P0** | **Auth + multi-tenancy + RBAC** | **W4** | Blocks ANY design partner. Tenant-scoped roles ("admin IN THIS tenant"), row-level isolation (Postgres RLS w/ FORCE RLS or per-tenant ledger), append-only role history ("who could access X on date T"). Fast path: reuse the BudgetHunter Supabase Auth+RLS pattern |
| **P1** | **Observability suite** — atom-level lineage UI + audit log + run history + cost dashboard | **W4** | Table stakes (Palantir ships interactive lineage, traces, P95, 30-day status, audit logs; Atlan leads Gartner). OntoForge HAS the substrate nobody else does (HEARTH per-cell provenance, ledger semiring, CostMeter, gate vote-tally) — SURFACE it. Differentiator: value-level lineage vs their column-level |
| **P1** | **Ask-flywheel write-back** + living prompt library/router/observation | **W4** | `discovery/cached_work.py` exists but the loop isn't closed. Novel cross-source ask → live data engineering → answer → WRITE BACK as a versioned, auto-described, referenceable ontology object (next ask instant). Pair with the prompt ROUTER (classify→prompt, logged) + RAG-over-prompts + out-of-library/confidence-divergence flagging. The compounding moat |
| **P2** | **Lazy usage/criticality recompute** | **W4** | Scale-time efficiency over HEARTH bitemporal + WARDEN drift + DBSP incrementality; never nightly-everything. "Pay only to recompute what's used/critical" — feeds the cost dashboard. Only bites at scale |
| **P2** | **Client-side anonymization toolkit** | **W5** | One-click anonymize/decipher, customer-held traceable-ID key, cloud computes on anonymized input. NO incumbent ships it — headline trust wedge + open-shell flagship. Correctly sequenced AFTER connectors/auth/observability (no value until real data flows through a multi-tenant product) |

### Reusable agent roster + business artifacts

| Pri | Item | Wave | Detail |
|-----|------|------|--------|
| **P0** | **Dev roster as reusable agents** — Orchestrator / Implementer / Adversarial-Tester / Reviewer / Integrator / Research / IP-Warden | **W3-COMPANY** | Make the roster real, under the sequential-commit / parallel-research discipline already proven in this repo. Agent-to-agent adversarial verification holds quality without a human in every loop |
| **P0** | **Four earliest GTM artifacts** (fastest ROI, build FIRST) | **W3-COMPANY** | (1) one-page pitch — MARKET_EDGE structural read ("nobody starts from messy RAW sources, resolves entities, validates, exports the whole estate"); (2) landing page w/ **pre-signup interactive demo** (best 2026 trial-activation tactic); (3) demo SCRIPT showing the granularity the words no longer carry — atom-level citation drill-down, calibrated abstention on an unanswerable question, AMBER portable-bundle exit; (4) pricing/compute-ledger CALCULATOR (turns CostMeter into a transparent quote — counters Palantir's opaque-billing complaint). These convert the FIRST conversation |
| **P1** | **Business agents** — Competitive-Monitor, GTM/Growth, Pitch/Demo, Pricing, Support/Triage | **W3-COMPANY** | Competitive-Monitor auto-refreshes MARKET_EDGE.md (window "open but closing"); others own ICP/tone/channel, keep one-pager+script current, run the compute-ledger calculator, triage. Build the 4 artifacts FIRST, automate upkeep with agents SECOND — a full agent swarm is premature until design partners exist |

**Pitfalls (decisive):**
- The pitch must NOT lead with "citations/lineage/semantic layer/ontology" — MARKET_EDGE is
  explicit these are now table stakes. The demo must show the **GRANULARITY** difference
  (per-value/per-temporal-version citations, calibrated abstention vs binary refusal, full-estate
  AMBER portability). Words won't differentiate; the inspectable drill-down will.
- The induction-from-messy-sources window is "open but closing" (AutoSchemaKG, ATOM/iText2KG,
  GraphRAG, Fabric IQ/Osmos converging, several open-source). Speed to connectors + a real
  customer beats further engine depth.
- **Per-tenant learning isolation:** when adding RBAC+RLS, audit that `tenant/priors.py`'s learning
  store is isolated under the SAME tenant boundary — never cross-tenant, or you leak one customer's
  naming/join patterns into another's (fatal trust breach).
- Connectors collide with the zero-network/offline invariants: keep them in the open-shell ring
  behind the contracts seam, gate live-network paths so the test suite and offline demo never
  exercise them (cassette/fixture connector tests), never let a connector import a closed-core
  internal (`test_ip_boundary.py` enforces direction).
- Cost-dashboard credibility cuts both ways: the CostMeter must capture real connector egress +
  compute + (later) LLM tokens — not just token estimates — before the pricing calculator is shown
  to a prospect.
