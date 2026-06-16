# Agentic Build Runbook — how OntoForge is built by a solo founder + an AI agent crew

This is the operating manual for running OntoForge's autonomous build waves. It
codifies how this repo has *actually* been built (M0–M14, the Engine Wave 1 typed-
relationship stack, the UI maturation, the strategy artifacts — 1400+ tests green,
keyless, deterministic, zero-network) so that future waves are **repeatable** and
run with **role separation + anti-reward-hacking discipline by default.**

It pairs with the **reusable agent roster** in [`.claude/agents/`](../.claude/agents/):
seven dev roles + five business roles, each a Markdown agent definition encoding its
job, its hard constraint, what it must not do, and the shared discipline below. This
runbook is the *process*; those files are the *actors*.

Source of truth: the OntoForge v2 white paper + the v2.1 build instructions (PART II
§11–20). Where this conflicts with the white paper, the conflict is a **spec
amendment to confirm at a human checkpoint** (logged in `docs/DEVIATIONS.md`), never
an autonomous override.

---

## 0. The constraint that shapes everything

**One person plus AI must ship an enterprise OS.** That means many agents in
parallel, the founder is never the bottleneck for routine work, and human attention
is spent only where it is irreplaceable. The whole design exists to make that
honest — especially the controls that stop the build from grading its own homework.

---

## 1. The roster (who does what)

**Dev roster (7)** — `.claude/agents/`:

| Agent | Job | Hard constraint |
|---|---|---|
| `orchestrator-planner` | owns the task dependency graph; schedules parallel disjoint-file crews; merges results | **cannot edit tests AND implementation in one task** |
| `implementer` | writes production code against a task contract | **never sees the holdout suite** for its own task |
| `adversarial-tester` | writes tests incl. the holdout; tries to break the implementer | separate identity; **editing a test to pass = hard fail** |
| `reviewer` | diffs for contract/style/security + IP-boundary leakage | flags any cross-boundary leakage |
| `integrator` | merges, runs corpus integration, owns CI; rebase-before-push | **cannot weaken a gate to make a merge pass** |
| `research-agent` | the §19 weekly/monthly/quarterly cadences + briefs | **cannot weaken a gate/guarantee/license** to hit a number |
| `ip-security-warden` | enforces closed-core isolation | **veto** on any change crossing the IP boundary |

**Business roster (5)** — `.claude/agents/`:

| Agent | Job |
|---|---|
| `competitive-monitor` | watch Foundry/Snowflake/Databricks/dbt+Fivetran/Fabric IQ+Osmos + the induction frontier; flag moves; keep `MARKET_EDGE.md` current |
| `gtm-strategist` | the wedge sequence (regulatory fire → data debt → ontology reconstruction) + the ICP |
| `pitch-writer` | the one-pager / deck / demo script / landing copy — **leads with granularity** |
| `pricing-analyst` | compute-at-cost + service fee + flat subscription; keeps the calculator honest |
| `support-success` | onboarding for the "lazy" user; FAQ; trial design |

The **adversarial-tester / implementer split is load-bearing** — it is the single
thing that stops the build from reward-hacking its own green checkmarks. Keep them
separate identities, always.

---

## 2. The spec is the source of truth

The white paper (+ the v2.1 doc) **is the spec.** Agents implement against it. Any
deviation an agent wants follows a TEMPER-like amendment calculus:

> **propose → justify against the spec → queue for a human checkpoint** (logged as a
> typed change-object in `docs/DEVIATIONS.md`, e.g. AMD-0001 Python-first, AMD-0002
> keyless deterministic tiers).

Agents do **not** silently reinterpret the spec. A reinterpretation that isn't a
logged amendment is a process violation.

---

## 3. The four human checkpoints (everything else is autonomous)

1. **Spec changes** — amendments to the white paper / v2.1 doc.
2. **Security & IP boundaries** — what's closed vs. exportable; anything touching
   the core engines (the `ip-security-warden`'s veto surfaces here).
3. **Gold-set signoff** — the annotated ground-truth ER/join labels the whole eval
   rests on (the gold-set α ≥ 0.8 annotation-quality bar).
4. **Release gates** — promotion past a phase exit (the `integrator` prepares the
   release; the human signs).

If a decision is not one of these four, the agents resolve it autonomously. When a
task *hits* one of these, the responsible agent **stops and flags** — it does not
self-resolve.

---

## 4. Task decomposition — the dependency graph

Decompose the module map (and each new feature) into a **~150–200-task dependency
graph**. Each task carries a **contract** (§14):

- **Inputs / preconditions** — which upstream tasks must be green (`addBlockedBy`).
- **Definition of done** — the *behavioral* contract (what, not which assertions).
- **Coverage + mutation thresholds** — line/branch floors; a mutation-score floor.
- **Parallelism tag** — can this run concurrently, and with what.
- **Holdout reference** — which suite the implementer is **forbidden to see**.

The orchestrator builds the graph (TaskCreate + `addBlocks`/`addBlockedBy`), makes
the critical path explicit, and tags what can run concurrently. **A task that would
edit both `src/` and its own holdout `tests/` is malformed — split it** into an
implementer task and an adversarial-tester task with separate owners.

---

## 5. The per-task agentic loop (nine steps)

For every task in the graph:

1. **Orchestrator** assigns the task + contract.
2. **Implementer** drafts against the contract — **no holdout access.**
3. **Implementer**'s own tests pass locally (`uv run pytest tests/ -q`).
4. **Adversarial-Tester** runs the **holdout suite** + mutation tests; tries to
   break it.
5. Failures → back to step 2 (a **bounded** iteration budget — don't thrash).
6. **Reviewer** checks contract compliance + style + IP/security boundaries.
7. **Integrator** merges, runs **corpus-level integration** on the §17 estates.
8. **CI gate** (cassettes / property tests) must stay green — **zero tokens.**
9. **Mark done** + update the dependency graph.

```
orchestrator ──contract──▶ implementer ──code──▶ (own tests green)
                               │                       │
                               ▼                       ▼
                        [NEVER sees holdout]   adversarial-tester ── holdout + mutation
                                                       │
                                  break? ──yes──▶ back to implementer (bounded)
                                                       │ no
                                                       ▼
                                                    reviewer ── contract / IP / security
                                                       │ APPROVE
                                                       ▼
                                                   integrator ── corpus integration + CI
                                                       │ green
                                                       ▼
                                              rebase ▶ single commit ▶ mark done
```

---

## 6. Anti-reward-hacking controls (baked in, not optional)

These are the reason "all tests green" can be trusted:

- **Holdout the implementer never sees.** Authorship of code and of its holdout is
  split across two identities. The implementer implements to *behavior*, not to
  assertions it has read.
- **Editing a test to make it pass is a HARD FAILURE.** Tests encode the contract.
  No relaxing an assertion, widening a tolerance, skipping a case, or `xfail`ing a
  real failure to manufacture green. The reviewer auto-rejects it; the integrator
  refuses to merge it.
- **Mutation testing catches vacuous tests.** A test that passes against a
  deliberately broken implementation does not constrain behavior — it is a finding.
- **Cost instrumentation flags expensive "passes."** The CostMeter / ledger cost
  rows mean a green solution that is secretly expensive (tokens / compute) is
  surfaced, not silently accepted.
- **Never weaken a gate.** The load-bearing gates — no-confidently-wrong (no wrong
  answer at confidence ≥ `tau_high`), the free-text robustness gate (≥70%
  answered-with-citations, 0 confidently-wrong), the aviation competency suite, the
  OQIR typechecker, the Meridian gold gate, the IP-boundary import guard, the
  offline / no-external-fonts-or-CDN invariant, the no-innerHTML SPA security rule,
  and the non-vendor payload ceiling — are fixed by root cause, never lowered. If
  new markup invalidates a test, adapt it to the new *behavior* while keeping every
  security/gate assertion intact.
- **Phase 0 first.** Verification infrastructure (property tests for the math
  substrate, LLM cassettes for zero-token CI, cost instrumentation, the live-data
  estates) precedes product code. New subsystems get their harness task scheduled
  before their implementation task.

---

## 7. The IP boundary (enforced, every wave)

Two concentric rings, designed in from day one (see `docs/IP_ARCHITECTURE.md`):

- **Closed core (proprietary):** `relationships`, `validation`, `ensemble`,
  `tenant`, `discovery`, `strata`, `temper`, `hearth`, `anvil`, `warden`,
  `lodestone`, `vista`, `amber`, `spine`, `aimodels` — the named inventions, the
  confidence-proxy scoring, calibration, voting aggregation, the cost/decision
  spine, the prompt loop, the per-tenant learning *mechanism*, the stratified-
  sampling strategy.
- **Open shell (exportable / open-source candidate):** `cdc`, `estates`, `server`,
  `pipeline`, `engineer`, the future client-side anonymizer.
- **Shared seam:** `contracts` + the `ledger` semiring — the only sanctioned
  cross-boundary channel.

The open shell imports the closed core **only through package entrypoints +
`contracts`**, never a closed-core internal submodule. The `ip-security-warden`
holds a **veto** here; the `reviewer` flags leakage in every diff; the
`tests/test_ip_boundary.py` AST guard enforces import direction in CI. **Tenant
isolation is a security boundary** — per-tenant learning never rolls up cross-tenant;
a prior bleed is a fatal trust breach, treated as a veto. The actual repo split /
open-sourcing decision is **human checkpoint #2.**

---

## 8. How to launch a wave (the operating discipline this repo runs on)

This is the chained-wave pattern proven across M0–M14 → Engine Wave 1 → the speed +
UI + memo wave (see `git log`: one integration commit per wave, in order).

1. **Research-informed.** The `research-agent` / `competitive-monitor` file a brief
   (two-layer labeled: verifiable fact vs `[ASSUMPTION]` judgment). This *informs*
   the wave; it never commits engine code and runs **in parallel** with the prior
   wave's build.
2. **Plan.** The `orchestrator-planner` decomposes the wave into a task contract
   graph and assigns **disjoint-file crews** — two crews may run concurrently ONLY
   if their file sets do not overlap (so the single integration commit absorbs both
   with no merge race). Closed-core and open-shell work parallelize cleanly; two
   edits to the same module do not.
3. **Build crews (parallel, disjoint files).** Each crew runs the §5 nine-step loop:
   implementer ↔ adversarial-tester, then reviewer.
4. **Integrate (serial).** The `integrator` merges the approved crews into one tree,
   runs corpus-level integration on the §17 estates + every gate:
   - `uv run pytest tests/ -q` (full suite)
   - `uv run pytest tests/m12 -q` (LODESTONE / free-text robustness gate)
   - `uv run pytest tests/m12/test_competency.py tests/meridian -q` (competency + gold)
   - `tests/test_ip_boundary.py` (IP guard) and `tests/server/test_spa.py` (SPA
     security + payload ceiling + token presence)
   - end-to-end smoke when the product surface changed:
     `uv run ontoforge demo meridian /tmp/mcm_demo` then serve (serve from the Bash
     tool in the background — the sandbox blocks `serve`'s process spawns).
   - `uv run ruff check src/` (debt confined to `temper/`).
5. **Single integration commit, rebase-before-push.** Committing waves are
   **serial**: only one is in flight; rebase onto the latest default branch first so
   there is no git race, then push **one** commit for the wave. (When operating
   under "do NOT git commit," the integrator hands back a green, staged,
   ready-to-commit tree and the human makes the commit/push call.) Commit trailer
   per the repo convention.
6. **Demo / verify.** Confirm the change in the running app, not just the suite.
7. **Mark done; the next chained wave starts.** Update the roadmap
   (`docs/AUTONOMOUS_ROADMAP.md`) and the dependency graph.

**Parallel vs. serial, in one line:** *non-committing research/business work runs in
parallel anytime; committing build waves run one at a time, rebased, with a single
integration commit each.*

---

## 9. Environment + invariants (every agent, every run)

- Run from the repo root with `uv` on PATH:
  `export PATH="$HOME/.local/bin:$PATH"` then `uv run …` (system python is 3.9 — too
  old; the project needs 3.12).
- **Keyless, deterministic, zero-network.** No API key is ever required; the NL
  layer is pure-python; tests never hit the network; the app ships **offline** (no
  external fonts/CDNs at runtime — only vendored Vega).
- **Determinism is a gate.** Any numba/numpy/vectorized rewrite must produce
  byte-identical FD/IND/MinHash output vs. the pure-Python reference, with a
  pure-Python fallback so a compile failure degrades gracefully (preserving the
  keyless guarantee).
- **Security invariant:** API data enters the DOM only via
  `el()`/`svgEl()`/`createTextNode` — never `innerHTML`/`outerHTML`. Non-vendor
  payload stays under the test-enforced ceiling — measure before adding copy/markup.
- **De-jargon is presentation-only:** user-facing labels are plain-language; code,
  URIs, API routes, and internal verdict names keep their engine codenames so
  routing/persistence still work.

---

## 10. Why this works (the existence proof)

A solo founder + this agent crew already built M0–M14, the Engine Wave 1
confidence/typing/validation/voting/tenant stack, and a non-trivial web product —
**deterministic, keyless, zero-network, with a full test suite** — under exactly
this discipline: sequential committing waves, parallel research, adversarial
agent-to-agent verification, a deep-research harness that *refuses to certify*
unfalsifiable claims, and an IP boundary guarded in CI. The roster in
`.claude/agents/` makes that team **reusable**: future waves run with role
separation and anti-reward-hacking controls **by default**, so the only scarce
resource — founder-hours — is spent on the four human checkpoints and the room, not
on grading the build's own homework.
