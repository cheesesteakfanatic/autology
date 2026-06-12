/* Exporter — portability as a visible feature. One button strikes an
   AMBER snapshot (POST /api/export); the shelf below lists the bundles
   the project holds. Renders the server's response defensively and
   degrades to honest CLI guidance when the endpoint isn't in this build. */

import { el, clear, api, errorNote, fmt } from "../core.js";

function kvRows(obj) {
  const rows = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v === null || ["string", "number", "boolean"].includes(typeof v)) {
      rows.push(el("tr", {}, el("td", {}, k), el("td", {}, v === null ? "∅" : String(typeof v === "number" ? fmt(v) : v))));
    }
  }
  return rows;
}

export function createExporterApp() {
  return {
    id: "exporter",
    title: "Exporter",
    tagline: "AMBER snapshots — take the world with you",
    glyph: "⇲",
    w: 480, h: 420, multi: false,

    mount(ctx) {
      const button = el("button", { class: "btn btn-forge", type: "button" },
        "⇲ strike an AMBER snapshot");
      const result = el("div", { class: "export-result" });
      const listLabel = el("span", { class: "section-label", style: "margin-top:1.5rem" }, "bundles");
      const list = el("div", { class: "export-list" });
      ctx.root.append(
        el("p", { class: "export-blurb" },
          "everything the estate believes — atoms, entities, provenance — sealed into one portable bundle. nothing is locked in."),
        button, result, listLabel, list);
      ctx.root.classList.add("app-exporter");

      async function refreshList() {
        clear(list).append(el("div", { class: "skeleton", style: "width:55%" }));
        try {
          const out = await api("/api/exports");
          const bundles = out.exports || [];
          clear(list);
          if (!bundles.length) {
            list.append(el("div", { class: "empty-note", style: "padding:0.75rem 0;text-align:left" },
              "no bundles yet — strike one above"));
            return;
          }
          for (const b of bundles) {
            if (typeof b === "string") {
              list.append(el("div", { class: "export-bundle mono" }, b));
            } else {
              list.append(el("div", { class: "export-bundle" },
                el("table", { class: "data" }, el("tbody", {}, kvRows(b)))));
            }
          }
        } catch (e) {
          clear(list).append(el("div", { class: "empty-note", style: "padding:0.75rem 0;text-align:left" },
            e.status === 404 || e.status === 405
              ? "bundle listing not exposed by this server build — `ontoforge export` works from the CLI"
              : String(e.message || e)));
        }
      }

      button.addEventListener("click", async () => {
        button.disabled = true;
        clear(result).append(el("div", { class: "skeleton", style: "width:70%" }));
        try {
          const out = await api("/api/export", {});
          clear(result).append(el("div", { class: "export-done" },
            el("span", { class: "section-label", style: "margin:0 0 0.5rem" }, "snapshot struck"),
            el("table", { class: "data" }, el("tbody", {},
              kvRows(typeof out === "object" && out !== null ? out : { result: String(out) })))));
          refreshList();
        } catch (e) {
          clear(result).append(
            e.status === 404 || e.status === 405
              ? el("div", { class: "empty-note", style: "text-align:left;padding:0.75rem 0" },
                  "export endpoint not exposed by this server build — run `ontoforge export` from the CLI; the bundle format is the same")
              : errorNote(e));
        } finally {
          button.disabled = false;
        }
      });

      refreshList();
      return {};
    },
  };
}
