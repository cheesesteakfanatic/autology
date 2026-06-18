# OntoForge — State of the Platform: honest assessment & next steps

_Synthesis of an adversarial, code-grounded audit (5 auditors, read-only, evidence-cited).
This is candid engineering judgment, not marketing. Date-stamped to the session that produced it._

## 0. One-paragraph reality

OntoForge is a **single-machine, keyless-deterministic semantic-data engine** plus a polished
three-mode web app, with genuinely well-engineered provenance, typed-join, and anonymization
primitives and a clean **LLM-ready seam that has never once run a live model**. It is a strong
solo-founder **demo / prototype**, not yet an operable multi-user product. Importantly, the repo's
own docs (README, ROADMAP, pitch) are honest — they tag assumptions vs. measured and defer scale/auth
as human checkpoints. The gap that matters is between the **external "operating system for autonomous
data engineering / petabyte" framing** and the code; the docs largely already concede it.

## 1. What we actually built (the real, good parts)

- **Full engine M0–M14**: content-addressed ledger + provenance semiring, the 4-tier decision spine,
  numpy-vectorized profiler (TANE FD / MinHash IND), STRATA (FCA lattice induction), ER (Fellegi–Sunter),
  HEARTH (bitemporal, per-cell provenance), TEMPER/ANVIL/WARDEN, RDF/OWL/SHACL export, LODESTONE
  (NL→OQIR with a type checker that **really rejects** unit/grain/phantom queries), VISTA, AMBER.
- **The §1 typed-relationship engine**: confidence-proxy signals → typed taxonomy (incl. the
  UNRELATED false-positive killer) → RoadSpy scout payload → DuckDB SQL-execute backward validation →
  reasoning-path voting.
- **Real test rigor** (not fake): ~1,900 deterministic, zero-network tests; the Meridian gold answers
  are **re-computed from the emitted corpus, not hardcoded**; warts are adversarially audited; the ER
  cascade has a proper **anti-leakage train/test hold-out with P/R/F1**; ~29 property-based tests.
- **Scale engineering within the single-node envelope**: a genuinely streaming large-CSV connector, a
  property-tested banded-hash IND scale guard, a joinability-preserving stratified `plan_subset`.
- **AI-native seam**: identity-preserving keyless `resolve_client`; `SecureModelClient`
  (PII redact → injection refuse → spotlight) interposed on the only egress path;
  `ValidatingModelClient` (schema-validate → retry → degrade to deterministic); an anonymizer with a
  **correct encrypt-then-MAC** construction that **loudly labels itself demo-grade**.
- **The web app** (today): Atelier (warm light) + Observatory (warm dark) redesign across Ask/Build/Studio.
- **Honest collateral**: README/ROADMAP/pitch tag `(measured)` vs `[assumption]`; the IP boundary is
  documented and tripwire-guarded by an AST import test.

## 2. What we missed — the gaps, by severity

### BLOCKER (fix before anyone else touches it / before any hosting)

1. **Zero authentication on the server, and Docker binds `0.0.0.0`.** Every endpoint is open —
   including state-mutating ones (`/api/engineer/apply`, `/api/workspace/build`, `/api/review`,
   `/api/export`, `/api/extract?format=csv`). CORS-localhost is not an access control. The shipped
   container would expose an unauthenticated read+write+export API to its network.
   _Evidence: `server/app.py:349` (only CORS+StaticFiles), Dockerfile final CMD `--host 0.0.0.0`._

### MAJOR (undermines the core claims; needed before pilots / due diligence)

2. **Never validated on data we didn't author.** Every gate (Meridian, aviation competency, ER gold
   pairs, relationship/join tests) is self-generated synthetic data whose answers were computed by the
   project's own code. The "real" FAA/NTSB sources were unreachable (HTTP 403 / 95MB) and **replaced by
   generation** (MANIFEST admits it). No independent/third-party labeled benchmark exists. The gates
   measure **self-consistency, not real-world accuracy**.
3. **The atom-level citation claim is untested for correctness.** All three answer gates only assert a
   citation *exists and resolves* (`atom_ids` non-empty, `get_atom` not None, `uri startswith atom://`).
   None check the produced citation points to the **right** (table, row_key, column) — and the gold YAMLs
   already carry that ground truth. A citation resolving to a wrong-but-existing atom passes every gate.
   The flagship "click the answer, see the exact source cell" is not proven.
4. **The LLM layer has never executed — not once.** Every "live" test monkeypatches
   `_build_live_adapter` to a cassette/mock, so the code that builds a real adapter is never run.
   **Latent bug surfaced by this:** there are **two incompatible `OpenAICompatAdapter` classes** — the
   activation seam wires the **untested, no-retry** one (`aimodels/openai_compat.py`) while the
   retry-hardened, unit-tested one (`ledger/models.py`) is dead code on that path. A single real run
   would have caught it. Real cost, latency, in-the-wild injection resistance, and schema-conformance of
   real model JSON are all unmeasured.
5. **Multi-tenancy is inert.** `TenantPriors` is instantiated **nowhere** in `src/`; the server builds
   Lodestone with `tenant_id=''`; criticality state is **process-global**. Everything lives under one
   global "" tenant — the "never cross-tenant" guarantee is real in unit tests but does nothing in the
   running system, and a hosted multi-user deployment would leak query patterns across users.
6. **Scale is single-node, RAM-bounded.** No spark/ray/dask/polars; the core pipeline loads whole
   tables into pandas/lists. Largest end-to-end demo is **~9,000-row Meridian**; the only 50k–200k figure
   is a two-kernel profiling micro-bench. "Petabyte/enterprise" is asserted, not measured. Two connector
   docstrings (`SqlConnector`, `ObjectStore`) **overclaim "constant memory"** for what is full in-memory
   materialization.
7. **No CI/CD, nothing deployed.** The ~1,900-test suite — the whole asset — is gated only by running
   `pytest` on your laptop. No `.github/`, no monitoring/error-tracking, Dockerfile never built, site
   never deployed, Cloudflare is plan-only.
8. **The "lazy user" one-click onboarding doesn't exist for real data.** One command works only for the
   3 **bundled** demo estates; a customer's own folder is a 6-stage CLI (`init→ingest→profile→induce→
   resolve→materialize→serve`); S3/Postgres need extra flags. No GUI upload, no "drop a folder → answer."

### MODERATE (real, but quality/polish — not urgent)

9. **Tiered compute is unexercised on the demo** — all spines are built `model_client=None`, so any
   ambiguous decision defers to human; T2/T3 adjudication is dead on the keyless demo. "Cost scales with
   ambiguity" is architecture, not a measured behavior.
10. **The "temperature-weighted LLM ensemble" is deterministic-only** — `default_experts()` are four
    heuristics; no model expert is wired; the typed-relationship `RelationshipGate` has *no* model seam at
    all (only comments). The framing overstates what runs.
11. **Modality is tabular CSV/Parquet + `.txt`/`.md` docs only** — no JSON/nested/JSONL/XML/PDF/log/
    streaming/multilingual ingestion. "Heterogeneous enterprise data" is in practice "heterogeneous CSV."
12. **PII redaction is a 24-name first-name gazetteer, not NER** — real names/surnames/non-Western names
    pass through unredacted to a live model. The keymap KDF does no passphrase stretching (bare HMAC, no
    PBKDF2/scrypt/Argon2), so a passphrase-sealed keymap is cheaply brute-forceable offline.
13. **The "zero confidently-wrong" guard is vacuously satisfied** (the engine answers the gold set
    correctly, so the assertion body rarely runs); answer-grading is **scalar-only** (no multi-row join
    correctness graded end-to-end).
14. **The Data Map canvas — the flagship viz — renders faint/washed**, especially in light mode.
15. **The IP two-ring split is designed + tripwire-guarded but not physically executed** (one repo, one
    `src/` tree); GTM is docs-only with zero users, illustrative pricing, and a stub signup form.

## 3. The single most important gap

Everything rests on **one unproven claim**: *does it reconstruct a correct ontology with trustworthy
citations on data we didn't author?* Scale, auth, LLM, and deploy are all secondary to proving **that**.
It is also the **cheapest** gap to close — and the one a technical buyer will probe first.

## 4. Next steps (prioritized)

**Tier 0 — Truth (cheap, highest leverage, do first):**
- Run the full pipeline on **one real, third-party, independently-labeled multi-table dataset** (a public
  schema-matching/FK benchmark, TPC-H with its known FKs, or a small real DB) and publish **measured**
  precision/recall for join typing, ER, and NL-answer accuracy.
- Add the **citation-correctness test**: map each produced `atom_id` back to (source, table, row_key,
  column) and assert set-equality against the gold YAMLs you already have. Cheap; huge trust payoff.
- Add a **CI workflow** (`.github/workflows/ci.yml`) running `uv sync && pytest && ruff` on push.

**Tier 1 — Make the claims true or soften them:**
- Fix the duplicate `OpenAICompatAdapter` (collapse to the retry-hardened one) and do **one real
  live-model smoke run** capturing cost/latency/JSON-conformance → then "LLM-validated" is honest.
- Either wire a model-expert into the ensemble or relabel it a "deterministic multi-signal gate."
- Run the full pipeline on one **~10M-row table**; publish wall-clock + peak RSS. Fix the two
  overclaiming connector docstrings. Make Plan mode the documented front door for big estates.
- Add a **JSON/JSONL flattening connector** (highest-value missing modality) or scope the claim.

**Tier 2 — Productize (only once Tier 0/1 prove it's worth it):**
- **Auth + a per-tenant world** (the Cloudflare DurableObject-per-tenant path) before any hosted pilot.
- A real lazy-user path: `ontoforge run --source <dir>` (chain the 6 stages) or web upload → cited answer.
- Actually `docker build` the image; deploy the site; wire the signup capture (it's a stub today).
- Production KMS + real NER for the anonymizer/PII path; add passphrase stretching to the keymap KDF.

**Tier 3 — Scale & moat (when a customer reason exists):**
- Warehouse push-down / out-of-core; the Rust core already in the roadmap.
- Physically execute the IP repo split before any open-sourcing; extend the import guard beyond server/cdc.

## 5. Framing recommendation

Align the external pitch to what the README already honestly says — **"single-node semantic data
platform for tabular sources, RAM-bounded, with a smart subsetter and an LLM-ready seam"** — not a
"petabyte OS." The docs are honest; the risk is the *pitch* getting ahead of them in front of a
technical buyer. Lead with the genuinely-differentiated, genuinely-built things: typed-join inference
with execution-grounded validation, atom-level provenance, and join-preserving anonymization — once the
first two are proven on data you didn't author.
