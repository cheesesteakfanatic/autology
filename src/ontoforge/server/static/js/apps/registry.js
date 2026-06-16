/* The app registry — every micro-app the OS can run. Apps never import
   each other; intents travel over the bus and the WM owns routing policy.
   An app spec: { id, title, tagline, glyph, w, h, multi, transient?,
   mount(ctx, params) -> appApi }.

   These are STUDIO's power tools plus the shared utilities (Explore record,
   Where this came from). ASK and BUILD are single-surface modes (js/surfaces/*)
   and are NOT windowed apps. Internal app ids are unchanged so the bus,
   spotlight and workspace persistence keep routing correctly; only the
   user-facing titles/taglines are de-jargoned. */

import { createCatalogApp } from "./catalog.js";
import { createDataMapApp } from "./datamap.js";
import { createConsoleApp } from "./console.js";
import { createReviewApp } from "./review.js";
import { createPulseApp } from "./pulse.js";
import { createInspectorApp } from "./inspector.js";
import { createEvidenceApp } from "./evidence.js";
import { createObservatoryApp } from "./observatory.js";

export function createRegistry() {
  const specs = [
    createCatalogApp(),       // Data Catalog
    createDataMapApp(),       // Data Map (id: "constellation")
    createConsoleApp(),       // Data-Engineering Console
    createReviewApp(),        // Confirm suggestions (id: "review")
    createPulseApp(),         // Activity (id: "pulse")
    createInspectorApp(),     // Explore record (id: "inspector")
    createEvidenceApp(),      // Where this came from (id: "evidence")
    createObservatoryApp(),   // Observatory — lineage/audit/runs/compute (id: "observatory")
  ];
  const byId = new Map(specs.map((s) => [s.id, s]));
  return {
    get: (id) => byId.get(id) || null,
    all: () => [...specs],
  };
}
