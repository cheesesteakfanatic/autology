# `ontoforge.estates` — Aviation hero estate: fixtures + gold artifacts

Whitepaper §12.4 (CEO-question suite corpus), §17.2.1 (AVIATION hero estate),
§17.3 (Estate A), §17.4 (Tier-2 gold artifacts), AMD-0006 (pinned schema-faithful
fixtures where live downloads are blocked).

## What this module owns

| Path | Role |
|---|---|
| `src/ontoforge/estates/aviation.py` | `load_estate()`, `load_gold_ontology()`, `load_competency_questions()`, `load_er_gold_pairs()`, table/key metadata |
| `src/ontoforge/estates/gold.py` | gold mini-ontology JSON → `contracts.Ontology` loader; competency-YAML loader |
| `src/ontoforge/estates/yamlite.py` | deterministic emitter/parser for a strict YAML-1.2 subset (PyYAML is not in the approved dependency set — §18 rule 2: implement the primitive yourself) |
| `scripts/build_aviation_fixtures.py` | the deterministic generator (seed=42) |
| `fixtures/aviation/` | committed pinned corpus (~1.1 MB, budget < 5 MB) |
| `tests/estates/` | determinism, wart-presence, gold-validity tests |

## Real downloads: what succeeded, what didn't (AMD-0006 report)

Attempted live downloads (2026-06-11):

- **OpenFlights `airports.dat` / `planes.dat` — SUCCEEDED** (HTTP 200; 1,127,225
  and 8,331 bytes). Pinned as trimmed seeds under `fixtures/aviation/_seed/`
  (`airports_us.csv`: 1,251 US airports with IATA codes; `planes.csv`: 246
  aircraft types) with raw-download SHA-256 hashes in `_seed/MANIFEST.json`
  (§17.6 reproducibility protocol: pin a snapshot coordinate per corpus).
- **FAA `registry.faa.gov/database/ReleasableAircraft.zip` — FAILED, HTTP 403**
  to non-browser clients, exactly as AMD-0006 documents.
- **NTSB `data.ntsb.gov` bulk `avall.zip` — reachable (HTTP 200) but unusable**:
  ~95 MB Microsoft Access database, far over the 5 MB fixture budget and not
  parseable with the approved dependency set. Treated as blocked per AMD-0006.

Consequently the registry/ASRS/NTSB/ERP tables are **generated faithful to the
documented real layouts** with §17.2.1's documented warts injected deliberately;
the OpenFlights seeds supply real airport names/cities and real aircraft-type
vocabulary so narratives and locale references are grounded in real entities.

`--refresh-seeds` re-downloads and re-trims the seeds (network); plain runs and
all tests read only the pinned seeds — zero network in CI (§18.4).

## The generated corpus

| File | Rows | Layout fidelity / warts |
|---|---|---|
| `faa_master.csv` | 2,500 | Real ReleasableAircraft MASTER columns; every field space-padded to its fixed layout width (trailing-space wart); `N-NUMBER` stored without the leading "N" (as in the real file); blank permissible fields (`YEAR MFR` ~4%, `AIR WORTH DATE` ~8%, `FRACT OWNER`, `STREET`); MODE S codes octal; **8 N-numbers reused** across two airframes each with disjoint `[CERT ISSUE, EXPIRATION]` windows (old row `STATUS CODE=D`, new row `V`) — the §17.2.1 temporal-identity ER trap |
| `faa_acftref.csv` | 119 | CODE/MFR/MODEL/TYPE-ACFT/TYPE-ENG/AC-CAT/NO-ENG/NO-SEATS/AC-WEIGHT/SPEED; joined via `MFR MDL CODE`; manufacturer-name variants split across codes (`ROCKWELL INTERNATIONAL CORP` vs `ROCKWELL INTL`, `BOEING` vs `THE BOEING COMPANY`, etc. — FAA's own documented wart) |
| `asrs_reports.csv` | 350 | ASRS export-shaped columns incl. dotted names (`PLACE.LOCALE REFERENCE`, `ALTITUDE.AGL.SINGLE VALUE`); 60% of rows (210) mention a registry tail number **inside the NARRATIVE free text** (structured↔unstructured join surface); ~12% of altitudes recorded in METERS with an `m` suffix (unit wart — two anchored values straddle the 10,000 ft threshold so suffix-ignorers get the wrong answer); ~4% blank altitudes; operator names include deliberate misspellings ("Untied Airlines", "Detla Air Lines", …) |
| `ntsb_events.csv` | 200 | eADMS-shaped columns; 75% reference registry aircraft; ~14% of `ACFT_REGIST_NMBR` values drop the leading "N" (documented wart); blank `INJ_TOT_F` cells are *unknown, not zero*; three events are pinned to reused tails (two in the OLD airframe's window, one in the NEW's) |
| `maintenance_erp.csv` | 600 | synthetic ERP per §12.4 ("synthetic ERP/maintenance source for cross-system pressure"); `COST` alternates `USD 1,234.56` / `1234.56` lexical forms (ANVIL bait); `OPERATOR_NAME` uses ERP-style variants of registry registrant names; 3 documented orphan tails (referential-break wart) |

## Gold artifacts (§17.4 Tier-2)

- **`gold/er_gold_pairs.csv`** — 1,746 true cross-source same-entity pairs,
  emitted *while generating* (correct by construction). Schema:
  `ENTITY_TYPE` (`aircraft`|`operator`), `ENTITY_ID` (cluster id), `LEFT_TABLE`,
  `LEFT_KEY`, `RIGHT_TABLE`, `RIGHT_KEY`, `NOTE`. Row keys follow the estate
  convention (key-column values, stripped, `|`-joined). Aircraft pairs link the
  master row to ASRS/NTSB/ERP records; every event date falls inside the cited
  registration window (tested), so reused tails resolve to the *temporally
  correct* airframe (`NOTE=temporal_reuse_trap:{old,new}` marks the trap rows).
  Operator pairs link each operator's canonical master row to cross-source rows
  bearing variant/misspelled names, plus master↔master registry-spelling pairs.
  The two airframes sharing a reused tail are distinct entities — no pair ever
  merges them (tested).
- **`gold/mini_ontology.json`** — 17 classes (scaled per the dev plan §4 from
  §17.4's 30–60): `Agent` ⊃ {`Organization` ⊃ {`Manufacturer`, `Operator`},
  `Person` ⊃ `Mechanic`}, `Aircraft`, `AircraftModel`, `Engine`, `Component`,
  `Place` ⊃ `Airport`, and event classes `Registration`, `SafetyEvent` ⊃
  {`IncidentReport`, `AccidentEvent`}, `WorkOrder`. United/datatyped properties:
  `altitude_agl` (float, dim m¹, unit ft), `cost` (currency¹, USD),
  `labor_hours` (s¹, h), `cruise_speed` (m·s⁻¹, mph), counts. 20 SHACL-ish
  shapes (min/max count, regex patterns, in-value enums, value ranges with
  units). Loadable via `estates.gold.load_gold_ontology` into
  `contracts.Ontology`; this is the **frozen ontology for the §11.3 de-risking
  vertical slice**. Class URIs are `onto://gold/aviation/{Name}`; every class
  carries a `prov_ref` and an intent-hash anchor (§3.4.4 spirit).
- **`gold/competency_questions.yaml`** — 18 questions (dev-plan §4 floor is 15;
  §17.4's ≥50 is the unscaled target), each with generator-computed gold answer
  and the source cells (`table`, `row_key`, `column`) justifying it: multi-hop
  (CQ-01/02/08/09/14), temporal as-of over the reuse trap (CQ-03/04/12),
  unit-sensitive incl. the meters wart below 10,000 ft (CQ-05/06/09),
  structured↔unstructured (CQ-07/08/13), aggregation with name-variant folding
  and blank-vs-zero semantics (CQ-02/09/10/11/15), **2 unanswerable abstention
  targets** (CQ-16/17: airframe hours, insurance — present in no source), and
  **1 trick-unit** (CQ-18 "altitude in dollars" → `reject_unit_mismatch`; the
  rejection is type-level: length vs currency dimensions in the gold ontology).

## Design decisions

1. **Gold answers are computed, not hand-typed.** The generator derives every
   answer from the rows it just emitted; tests *independently recompute* the
   high-risk ones (CQ-05/07/09/10/12) from the committed CSVs. This removes the
   gold-vs-fixture drift failure mode and keeps the anti-reward-hacking rule
   honest (the suite cannot pass with hardcoded answers).
2. **Determinism by construction** (§18.4): a single `random.Random(42)` in a
   fixed call order; sorted iteration everywhere a set/dict could leak order;
   no wall-clock in generated artifacts; csv `lineterminator="\n"`. Tested:
   two runs byte-identical, and the committed fixtures byte-match regeneration
   (drift guard, in the spirit of §17.6's pinned-snapshot CI track).
3. **Warts are anchored, not just sampled.** Each trap that a competency
   question depends on is guaranteed by construction (e.g. a 3500 m altitude on
   a Descent row so the naive ft-reading changes CQ-05's answer; an NTSB
   fuel-exhaustion event whose registration number drops the "N").
4. **Row-key convention** shared by gold pairs and citations: key-column values
   stripped of FAA padding, `|`-joined (`faa_master` keys are
   `N-NUMBER|SERIAL NUMBER`, which keeps reused tails unambiguous).
5. **yamlite** emits/parses a strict YAML-1.2 subset (JSON scalars, 2-space
   block style) so the competency artifact is real YAML without adding PyYAML.
6. **Loading is wart-preserving**: `load_estate` reads everything as `str` with
   `keep_default_na=False`; cleaning is ANVIL's job (M8), not the loader's.

## Deviations / approximations (within AMD-0006's mandate)

- Registry scale is 2.5 k rows, ACFTREF 119, ASRS 350, NTSB 200 (AMD-0001
  fixture-scale rescaling; layouts faithful, volumes scaled).
- Deregistered airframes (the reuse trap's old rows) live in `faa_master.csv`
  with `STATUS CODE=D`; the real FAA ships them in a separate dereg file. Kept
  in one table so the temporal trap is reachable by the M5/M6 slice without a
  sixth source.
- `REGION` codes use a documented approximation of FAA regional groupings
  (states → `1/2/3/5/7/C/S/A`); `COUNTY` is a random 3-digit code.
- ASRS column subset: the real export has dozens of columns; the 11 emitted are
  the ones the estate's joins and questions exercise (dotted names preserved).
- The gold ontology has 17 classes vs §17.4's 30–60 (dev-plan §4 scales this to
  ~20 for the v0 pass).
- OpenFlights data is ODbL-ish community data used only as a name/vocabulary
  seed for synthetic rows; FAA/NTSB/ASRS layouts are US-Gov public domain.

## Regenerating

```bash
uv run python scripts/build_aviation_fixtures.py                  # from pinned seeds
uv run python scripts/build_aviation_fixtures.py --refresh-seeds  # re-download seeds (network)
uv run pytest tests/estates -q
```
