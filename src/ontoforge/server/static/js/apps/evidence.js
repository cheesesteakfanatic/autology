/* Evidence — the provenance drill as a window. Two modes: source-atom
   chips fetched live from /api/atoms, or the full derivation tree from
   /api/provenance (sums render as "any of", products as "all of").
   Spawned as a CHILD of the window that cited the value; closing the
   parent closes the evidence with it. Transient: not persisted. */

import { el, clear, api, errorNote } from "../core.js";

const ATOM_URI_RE = /^atom:\/\/([^/]+)\/([^/]+)\/(.+?)(?:#(.*))?$/;

/** source › table › row # column — the atom URI rendered as a path. */
function atomPath(uri) {
  const m = ATOM_URI_RE.exec(uri || "");
  if (!m) return el("div", { class: "atom-path" }, uri || "");
  const [, source, table, row, column] = m;
  return el("div", { class: "atom-path" },
    source, el("span", { class: "sep" }, "›"),
    table, el("span", { class: "sep" }, "›"),
    row,
    column ? el("span", { class: "col" }, ` #${column}`) : null);
}

function atomChip(atom) {
  return el("div", { class: "atom-chip" },
    atomPath(atom.uri),
    el("div", { class: "atom-value" }, atom.value === null ? "∅" : String(atom.value)),
    el("div", { class: "atom-id" }, `⌗ ${atom.atom_id}`));
}

function provNodeView(node) {
  if (node.kind === "atom") {
    return atomChip({ uri: node.uri, value: node.value, atom_id: node.atom_id });
  }
  if (node.kind === "one" || node.kind === "zero") {
    return el("div", { class: "atom-id" }, node.kind === "one" ? "⊤ (trivially derived)" : "⊥ (no support)");
  }
  const label = node.kind === "sum" ? "any of" : "all of";
  const kids = el("div", { class: "prov-children" }, node.terms.map(provNodeView));
  const twist = el("span", { class: "twist" }, "▾");
  const head = el("button", {
    class: "prov-op", type: "button",
    onclick: () => {
      const open = !kids.hidden;
      kids.hidden = open;
      twist.textContent = open ? "▸" : "▾";
    },
  }, twist, label, el("span", { class: "arity" }, `(${node.terms.length})`));
  return el("div", { class: "prov-node" }, head, kids);
}

export function createEvidenceApp() {
  return {
    id: "evidence",
    title: "Evidence",
    tagline: "source atoms & derivations",
    glyph: "⌗",
    w: 420, h: 460, multi: true, transient: true,

    mount(ctx, params) {
      const contextLine = el("div", { class: "evidence-context mono" });
      const body = el("div", { class: "evidence-body" });
      ctx.root.append(contextLine, body);
      ctx.root.classList.add("app-evidence");

      function showAtoms(atomIds, label) {
        contextLine.textContent = label || "";
        ctx.setTitle("Evidence — source atoms");
        clear(body);
        body.append(el("div", { class: "section-label" },
          `${atomIds.length} source atom${atomIds.length === 1 ? "" : "s"}`));
        for (const id of atomIds) {
          const slot = el("div", { class: "atom-chip" },
            el("div", { class: "atom-id" }, `⌗ ${id}`),
            el("div", { class: "skeleton", style: "width:70%" }));
          body.append(slot);
          api(`/api/atoms/${encodeURIComponent(id)}`)
            .then((atom) => slot.replaceWith(atomChip(atom)))
            .catch((e) => slot.replaceWith(el("div", { class: "atom-chip" },
              el("div", { class: "atom-id" }, `⌗ ${id}`), errorNote(e))));
        }
      }

      async function showProvenance(provRef, label) {
        contextLine.textContent = label || "";
        ctx.setTitle("Evidence — derivation");
        clear(body).append(el("div", { class: "skeleton", style: "width:55%" }));
        try {
          const out = await api(`/api/provenance/${encodeURIComponent(provRef)}`);
          clear(body).append(
            el("div", { class: "section-label" },
              `derivation — ${out.n_atoms} atom${out.n_atoms === 1 ? "" : "s"} · ref ${out.prov_ref}`),
            provNodeView(out.tree));
        } catch (e) {
          clear(body).append(errorNote(e));
        }
      }

      function show(p) {
        if (p.atomIds) showAtoms(p.atomIds, p.label);
        else if (p.provRef) showProvenance(p.provRef, p.label);
      }

      show(params);
      return { show };
    },
  };
}
