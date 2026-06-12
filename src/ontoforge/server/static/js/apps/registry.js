/* The app registry — every micro-app the OS can run, in dock order.
   An app spec: { id, title, tagline, glyph, w, h, multi, transient?,
   mount(ctx, params) -> appApi }. Apps never import each other; intents
   travel over the bus and the WM owns routing policy. */

import { createAskApp } from "./ask.js";
import { createConstellationApp } from "./constellation.js";
import { createInspectorApp } from "./inspector.js";
import { createEvidenceApp } from "./evidence.js";
import { createReviewApp } from "./review.js";
import { createDashboardsApp } from "./dashboards.js";
import { createPulseApp } from "./pulse.js";
import { createExporterApp } from "./exporter.js";

export function createRegistry() {
  const specs = [
    createAskApp(),
    createConstellationApp(),
    createInspectorApp(),
    createEvidenceApp(),
    createReviewApp(),
    createDashboardsApp(),
    createPulseApp(),
    createExporterApp(),
  ];
  const byId = new Map(specs.map((s) => [s.id, s]));
  return {
    get: (id) => byId.get(id) || null,
    all: () => [...specs],
  };
}
