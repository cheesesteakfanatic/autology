/* Data Catalog — browse datasets to add, grouped by domain (TOC first),
   search across all domains, add a dataset (folder/file path or drop), and
   kick off a model build. Each row shows name, domain, counts and a build-
   status pill. Adding is optimistic; the Data Map animates from real events.
   Contract: GET /api/catalog → {datasets,domains}; GET /api/workspace/state
   → {datasets,built,stats}; POST /api/workspace/build {dataset_ids,mode} →
   {job_id}. The Data Map app polls and owns the live join animation; here we
   request the build and announce it on the bus. */

import { el, clear, api, errorNote, fmt, toast } from "../core.js";

const BUILD_CAP = 25;

export function createCatalogApp() {
  return {
    id: "catalog",
    title: "Data Catalog",
    tagline: "add datasets, build a model",
    glyph: "▦",
    w: 640, h: 580, multi: false,

    mount(ctx) {
      ctx.root.classList.add("app-catalog");
      const search = el("input", {
        class: "catalog-search", type: "text", spellcheck: "false",
        placeholder: "find a dataset or column",
        oninput: () => render(),
      });
      const addBtn = el("button", { class: "btn btn-forge", type: "button", onclick: openAddSheet }, "Add data");
      const buildBtn = el("button", {
        class: "btn", type: "button", disabled: "disabled",
        title: "build a model from the selected datasets",
        onclick: build,
      }, "Build map");
      const toc = el("div", { class: "catalog-toc" }, el("div", { class: "skeleton-card" }));
      const sheet = el("div", { class: "add-sheet", hidden: "hidden" });
      ctx.root.append(
        el("div", { class: "catalog-bar" }, addBtn, buildBtn),
        search, sheet, toc);

      let catalog = null;
      let selected = new Set();      // dataset ids chosen for the build
      let openDomains = new Set();

      function statusPill(ds) {
        const s = ds.build_status || ds.status || (selected.has(ds.id) ? "selected" : "not_modeled");
        const map = {
          modeled: ["Modeled", "pill-modeled"],
          building: ["Building…", "pill-building"],
          not_modeled: ["Not yet modeled", "pill-neutral"],
          selected: ["Selected", "pill-selected"],
          needs_attention: ["Needs attention", "pill-attention"],
        };
        const [label, cls] = map[s] || map.not_modeled;
        return el("span", { class: `status-pill ${cls}` }, label);
      }

      function datasetRow(ds) {
        const on = selected.has(ds.id);
        const row = el("div", { class: `catalog-row${on ? " selected" : ""}` });
        const drawer = el("div", { class: "catalog-drawer", hidden: "hidden" });
        const head = el("button", {
          class: "catalog-row-head", type: "button",
          "aria-expanded": "false",
          onclick: () => {
            const open = drawer.hidden;
            drawer.hidden = !open;
            head.setAttribute("aria-expanded", open ? "true" : "false");
            if (open && !drawer.dataset.filled) fillDrawer(drawer, ds);
          },
        },
          el("input", {
            class: "row-check", type: "checkbox", checked: on ? "checked" : null,
            "aria-label": `select ${ds.name} for the build`,
            onclick: (e) => { e.stopPropagation(); toggleSelect(ds.id); },
          }),
          el("span", { class: "row-name" }, ds.name),
          ds.domain ? el("span", { class: "row-domain" }, ds.domain) : null,
          el("span", { class: "row-counts mono" },
            `${fmt(ds.cols ?? (ds.columns ? ds.columns.length : 0))} cols · ${fmt(ds.rows ?? 0)} rows`),
          statusPill(ds));
        row.append(head, drawer);
        return row;
      }

      function fillDrawer(drawer, ds) {
        drawer.dataset.filled = "1";
        clear(drawer);
        drawer.append(
          ds.description ? el("p", { class: "ds-desc" }, ds.description) : null,
          el("div", { class: "ds-cols" },
            (ds.columns || []).slice(0, 40).map((c) => el("span", { class: "ds-col mono" }, c))),
          el("div", { class: "ds-source mono" }, ds.source || ""),
          el("div", { class: "ds-row-actions" },
            el("button", {
              class: "range-link", type: "button",
              onclick: () => ctx.emit("studio:show-map", {}),
            }, "See in Data Map →"),
            el("button", {
              class: "ds-remove", type: "button",
              title: "remove from the build — your source files are not deleted",
              onclick: () => confirmRemove(ds),
            }, "Remove")));
      }

      function confirmRemove(ds) {
        if (!selected.has(ds.id)) { toast(`${ds.name} is not in the build`, { kind: "info" }); return; }
        if (window.confirm(`Remove ${ds.name}? The model will rebuild without it. Your source files are not deleted.`)) {
          selected.delete(ds.id);
          updateBuildBtn();
          render();
        }
      }

      function toggleSelect(id) {
        if (selected.has(id)) selected.delete(id); else selected.add(id);
        if (selected.size > BUILD_CAP) {
          selected.delete(id);
          toast(`You can build from at most ${BUILD_CAP} datasets at once.`, { kind: "warn" });
        }
        updateBuildBtn();
        render();
      }

      function updateBuildBtn() {
        buildBtn.disabled = selected.size === 0;
        buildBtn.textContent = selected.size ? `Build map (${selected.size})` : "Build map";
      }

      function render() {
        const q = search.value.trim().toLowerCase();
        clear(toc);
        if (!catalog || !catalog.datasets) {
          toc.append(el("div", { class: "empty-note" },
            "No data yet. Add a folder of CSV or Parquet files and OntoForge will build a model from it."));
          return;
        }
        const match = (ds) => !q || ds.name.toLowerCase().includes(q) ||
          (ds.columns || []).some((c) => String(c).toLowerCase().includes(q));
        const byDomain = new Map();
        for (const ds of catalog.datasets) {
          if (!match(ds)) continue;
          const dom = ds.domain || "auto-grouped";
          if (!byDomain.has(dom)) byDomain.set(dom, []);
          byDomain.get(dom).push(ds);
        }
        if (!byDomain.size) {
          toc.append(el("div", { class: "empty-note" }, "no datasets match that search"));
          return;
        }
        for (const [dom, rows] of [...byDomain].sort((a, b) => a[0].localeCompare(b[0]))) {
          const open = q ? true : openDomains.has(dom);
          const body = el("div", { class: "domain-body", hidden: open ? null : "hidden" });
          const totalRows = rows.reduce((a, r) => a + (r.rows || 0), 0);
          const header = el("button", {
            class: "domain-header", type: "button", "aria-expanded": open ? "true" : "false",
            onclick: () => {
              if (openDomains.has(dom)) openDomains.delete(dom); else openDomains.add(dom);
              render();
            },
          },
            el("span", { class: "domain-twist" }, open ? "▾" : "▸"),
            el("span", { class: "domain-name" }, dom),
            el("span", { class: "domain-count" }, `${rows.length} dataset${rows.length === 1 ? "" : "s"} · ${fmt(totalRows)} records`));
          for (const ds of rows) body.append(datasetRow(ds));
          toc.append(el("div", { class: "domain-group" }, header, body));
        }
      }

      function openAddSheet() {
        sheet.hidden = false;
        clear(sheet);
        const pathInput = el("input", {
          class: "add-path mono", type: "text", spellcheck: "false",
          placeholder: "/path/to/data  (a folder of CSV/Parquet, or one file)",
        });
        const domainInput = el("input", {
          class: "add-domain", type: "text", spellcheck: "false",
          placeholder: "domain label (optional — auto-grouped if blank)",
        });
        const drop = el("div", { class: "add-drop" }, "or drop files here");
        drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
        drop.addEventListener("dragleave", () => drop.classList.remove("over"));
        drop.addEventListener("drop", (e) => {
          e.preventDefault(); drop.classList.remove("over");
          const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
          if (f) { pathInput.value = f.name; toast("drop the folder path or use the CLI to ingest large drops", { kind: "info" }); }
        });
        sheet.append(
          el("div", { class: "add-head" },
            el("span", { class: "section-label" }, "Add data"),
            el("button", { class: "sources-close", type: "button", onclick: () => { sheet.hidden = true; } }, "×")),
          el("div", { class: "add-tabs" },
            el("span", { class: "section-label" }, "Point to a folder/file"),
            pathInput, domainInput,
            el("span", { class: "section-label", style: "margin-top:0.75rem" }, "Drop files"),
            drop),
          el("div", { class: "add-actions" },
            el("button", { class: "btn btn-forge", type: "button", onclick: () => addData(pathInput.value, domainInput.value) }, "Add"),
            el("button", { class: "btn", type: "button", onclick: () => { sheet.hidden = true; } }, "Cancel")));
        requestAnimationFrame(() => pathInput.focus());
      }

      async function addData(path, domain) {
        path = String(path || "").trim();
        if (!path) { toast("point to a folder or file first", { kind: "warn" }); return; }
        sheet.hidden = true;
        // optimistic: the new dataset surfaces immediately as Building…
        toast("adding dataset — the engine will read it and build the model", { kind: "info" });
        try {
          await api("/api/catalog", { source: path, domain: domain || null });
        } catch {
          // the contract's GET /api/catalog is read; add may post here or be
          // a CLI op in some builds — guide honestly without dead-ending.
          toast("this build adds data via `ontoforge init --source` — then it appears here", { kind: "info" });
        }
        await refresh();
      }

      async function build() {
        if (!selected.size) return;
        const ids = [...selected];
        buildBtn.disabled = true;
        try {
          const out = await api("/api/workspace/build", { dataset_ids: ids, mode: "replace" });
          if (out && out.job_id) {
            toast("building the model — watch the Data Map", { kind: "ok" });
            ctx.emit("studio:build-started", { job_id: out.job_id, dataset_ids: ids });
          } else {
            toast("build requested", { kind: "info" });
          }
        } catch (e) {
          toast(e.status === 404 || e.status === 405
            ? "this build models via the CLI — `ontoforge` builds the same model"
            : "couldn't start the build — see the CLI", { kind: "warn" });
        } finally {
          buildBtn.disabled = selected.size === 0;
        }
      }

      async function refresh() {
        clear(toc).append(el("div", { class: "skeleton-card" }));
        try {
          catalog = await api("/api/catalog");
          // pre-select already-modeled datasets so Build map reflects reality
          let ws = null;
          try { ws = await api("/api/workspace/state"); } catch { ws = null; }
          if (ws && Array.isArray(ws.datasets)) selected = new Set(ws.datasets);
          // open the newest-added domain by default
          if (catalog.domains && catalog.domains.length && !openDomains.size) {
            openDomains.add(catalog.domains[catalog.domains.length - 1].name);
          }
        } catch (e) {
          catalog = null;
          clear(toc).append(
            e.status === 404 || e.status === 405
              ? el("div", { class: "empty-note" },
                  "No data yet. Add a folder of CSV or Parquet files and OntoForge will build a model from it.")
              : errorNote(e));
          updateBuildBtn();
          return;
        }
        updateBuildBtn();
        render();
      }

      ctx.on("world:reload", refresh);
      refresh();
      return { refresh };
    },
  };
}
