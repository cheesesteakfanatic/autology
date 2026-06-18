# OntoForge — UX vision: language-first, provenance-rich, scale-proof

The guiding realization: **at thousands of datasets, browse-first UI collapses.** Menus, chip
lists, and window desktops work at 10 datasets and fail at 1,000. Every surface must invert to
**language-first + search-first, grounded in the live ontology**, with browsing demoted to a
ranked, faceted, secondary affordance. The command input *is* the navigation. This is also the
product's real edge (common-language + atom-level provenance), so the UI should make it the spine.

Visual language is settled: **Atelier** (warm light, default) / **Observatory** (warm dark, toggle),
one shared token system, system fonts (serif display, sans UI, mono metrics), real depth, a single
burnt-amber accent. This doc is about LAYOUT + INTERACTION per surface, not the palette.

## Ask — the questioner. The answer is the product, not the box.

- **Grounded typeahead.** As the user types, suggest real entities / measures / questions from the
  induced ontology (use `/api/ontology` + `/api/search` + criticality ranking) — not generic chips.
- **The answer surface** (the centerpiece): the value LARGE and clear; the query echoed in **plain
  English** ("summed `freight_cost` over 1,190 Shipments where status = open"); **inline, expandable
  provenance** — "where this came from" opens the exact source rows/cells, clickable; a confidence
  read; and 2–3 **smart follow-ups** ("break this down by month", "compare to last year").
- **Honest abstention** stays first-class — when it won't bluff, it says so, beautifully.
- **Empty state** = "what your data can answer", organized by the real top entities (criticality-
  ranked), each expanding to example questions — so a new user sees capability, not a blank box.
- Feel: Perplexity clarity + Bloomberg trust + Linear calm. Streaming-feel, instant, never a chat-
  bubble log; history is a quiet rail, not clutter.

## Build — the builder. Tableau-grade, common-language, scale-proof.

- **Primary input: a natural-language view bar.** "freight cost by month for the battery supplier as
  a line chart" → parsed into a structured spec {measure, breakdown(s), filter(s), viz} → a real chart.
- **Refinement: Tableau-style shelves** the language populates and the user tweaks — Measure /
  Break down by / Filter / Chart type. Drag/click to adjust; the NL and shelves stay in sync.
- **Field finding by faceted search, ranked by criticality** — NOT a flat list. A search box with
  facets (dataset / domain / type / measure-vs-dimension); results ranked by `/api/criticality`. This
  is the explicit fix for "the left buttons make no sense at thousands of datasets."
- **Real charts** via the vendored Vega: bar / line / area / stacked / table / big-number, proper axes,
  warm-palette marks, hover tooltips.
- **Dashboards**: pin panels into an arrangeable, savable, nameable grid; each panel carries its
  plain-English definition + provenance + a one-click Extract (CSV) / Export. A dashboard is a
  first-class, shareable artifact.
- Feel: "Tableau, but you say what you want and it builds it, and it scales because you search."

## Studio — the engineer. A coherent cockpit, not a window desktop.

- **The Data Map is the centerpiece** — the ontology graph is the engineer's mental model; make it
  beautiful, central, high-contrast (confirmed joins solid, likely dashed, silos honest), pan/zoom,
  click a node → its fields/lineage/criticality.
- **Kill the floating windows.** One coherent layout: map center, a purposeful left rail (Catalog /
  Map / Console / Confirm / Activity / Observatory), and the **plain-English engineering console as a
  persistent bottom command bar** ("merge these two suppliers", "this column is a date") with preview +
  undo.
- **Confirm queue** (suggested joins/merges to approve) is prominent — autonomous proposals, human
  in the loop, one-click confirm/reject feeding the priors.
- Observability (lineage/audit/runs/compute) one click away, not a competing window.

## Dock — dynamic, macOS-grade.

- Proximity **magnification** (fisheye: icons scale by cursor distance), smooth spring easing, hover
  labels, active-app indicator, running-dot. Pure-JS transform on `mousemove`, reduced-motion aware.

## Cross-cutting

- One command grammar across modes (Ask question / Build view / Studio op) — same input feel.
- Criticality is the universal ranking signal (what to suggest, what to show first) — already computed.
- Provenance is always one click from any number, anywhere.
- Keep el()/createTextNode security, offline system-fonts, the gates. Charts use vendored Vega only.
