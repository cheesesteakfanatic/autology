# Engine SOTA research — join/relationship inference (2024–2026, verified)

Deep-research run (111 agents, 3-vote adversarial verification). **22 verified technical claims; the
strategic "billion-dollar company" thread produced zero claims that survived verification** — strategy
is opinion, not falsifiable, so it's handled as a synthesis memo elsewhere, not as "verified research."
This doc is the THREAD-2 (engine) result and directly hardens Engine Wave 1.

## Headline: the literature validates our exact architecture
**Tursio** (*Scalable Join Inference for Large Context Graphs*, arXiv:2603.04176, Mar 2026) is the
directly OntoForge-shaped prior art and the strongest source: a two-stage **heuristics-first →
LLM-as-selective-adjudicator-on-computed-evidence** spine — prune the candidate space with lightweight
statistics, then invoke an LLM only on the pruned candidates, feeding it **only names + sample values
(never raw data)** — with an **execute-the-join validation gate** and **precision-over-recall**
calibration. That is exactly Engine Wave 1 (`relationships/` → ensemble adjudication → `validation/`).

## Verified design rules (fold into Wave 1)

1. **Never gate on a single metric or on name matching** (3-0). Fixed per-metric thresholds are the core
   failure mode — a valid join can sit at Jaccard 0.09 while the threshold low enough to catch it
   destroys precision. Set-overlap alone scores F≈0.20–0.35; name-only recall collapses to ~0.6 on
   noisy names; embeddings/LLM-in-isolation underperform classical. **Fuse signals; don't threshold
   individually** (OmniMatch: +14% F1 with *no* user thresholds via signal combination).

2. **The vetted metric menu per column-pair** (3-0) — compute all, then fuse:
   - set **containment** |A∩B|/|A| in **both directions** (asymmetric — the IND / PK-FK signal)
   - **MinHash-Jaccard** over full value sets (symmetric overlap)
   - **Jaccard on infrequent tokens** (catches format-variant joins, "St" vs "Street") ← *new signal to add*
   - **cardinality proportion** K = min(|A|,|B|)/max(|A|,|B|) (granularity match — kills same-value-different-granularity)
   - **uniqueness / distinct-to-rows ratio** (PK signal)
   - **value-distribution divergence (JSD)** — the false-positive killer: catches related cols with
     synonyms/low overlap AND cols that share values but disagree on frequency
   - **entropy** (low-entropy status/boolean cols make poor keys)
   - value-semantics + metadata/name embeddings (later, via aimodels)
   - **forward & reverse join-size / fan-out**

3. **Execute the candidate join as a validation gate** (3-0), calibrated **precision-over-recall** —
   false-positive joins corrupt downstream SQL/ontology. Tursio: validate by actually running the join;
   commit/abstain metric = "top-3 candidates within 0.9 confidence." (This is `validation/`.)

4. **Concrete default thresholds** (Tursio, author-tuned, not proven optima — adopt as defaults, keep tunable):
   - **PK candidate** when `distinct ≥ 0.95·rows` AND within ±5% of the table's max distinct (x=0.95 best).
   - **IND candidate** scored by a 5-component score, **pruned at 0.4**.

5. **Context-dependent signal weighting** (3-0): size/overlap signals dominate **clean relational**
   estates; metadata + value-semantics dominate **messy lakes**. Detect the estate type and re-weight —
   don't use one global formula. (A per-estate weighting profile; complements per-tenant priors.)

6. **Tier the IND/discovery engine by data size & compute** (3-0): IND discovery is **NP-hard /
   W[3]-complete** — at billion-row scale you **must approximate, sample, and parallelize** (no
   exhaustive enumeration). In-RAM DeMarchi when it fits; Binder for exact >GB; Faida
   approximate-but-complete for candidate generation + cheap exact re-validation; Sindy on ≥8 cores.
   → validates our `pipeline/scale.py` + the doc's schema-informed stratified sampling.

7. **Ensembles beat single-criterion** (3-0) for joinable-column discovery — direct support for the
   multi-signal stack + reasoning-path voting.

*Refuted/unverified:* the claim "the distribution-based matcher is the single best method" did NOT
survive (0-3) — so JSD is a **strong fused signal, not the sole oracle** (our design already fuses).

## Wave-1.5 refinements (apply after Wave 1 lands)
- Add the **infrequent-token Jaccard** signal to `relationships/signals.py`.
- Adopt Tursio's **PK band (0.95/±5%)** and **IND prune at 0.4** as documented defaults.
- Add the **top-3-within-0.9 commit/abstain** calibration metric to the relationship gate.
- Add a **per-estate weighting profile** (clean-relational vs messy-lake) feeding the score fusion.

## THREAD 1 (strategy) — honest status
No verified claims survived. Strategic positioning (billion-dollar vibe-coded company, GTM wedges,
zero-margin-compute credibility, anonymization-as-trust) will be delivered as a reasoned **strategy
memo** synthesizing the *verifiable* competitive facts already in `docs/MARKET_EDGE.md`, clearly
labeled as judgment, not fact-checked research.
