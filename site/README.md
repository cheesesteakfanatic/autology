# OntoForge — marketing site

A polished, static marketing surface for OntoForge: the landing page, a canned
interactive demo, and an interactive compute-ledger pricing calculator. Vanilla
HTML/CSS/JS, **no build step**, **ships entirely offline** (system font stacks,
inline data-URI grain, zero external fonts/CDNs/network at runtime).

It matches the product's matured warm-midcentury design system (desaturated warm
palette, espresso ink, system serif headlines, mono for data, one marigold
primary action per view, teal = confirmed/cited) — the tokens are distilled from
`src/ontoforge/server/static/style.css`.

## Files

| File | What it is |
|---|---|
| `index.html` | The landing page — hero (the one-line mandate + granularity/trust/exit differentiators), the 3 modes (Plan / Build / Ask), how-it-works pipeline, the trust architecture (client-side anonymization), compute-at-cost economics, measured proof numbers, and an email-capture stub. |
| `style.css` | The single shared stylesheet for all three pages (design tokens + page-specific sections for the demo and pricing calculator). |
| `demo.html` + `demo.js` | The canned, deterministic, offline "see the magic" walk-through of autonomous join discovery. No backend. |
| `pricing.html` | The interactive compute-ledger calculator (inline vanilla JS): volume slider + complexity + plug-and-play/bespoke → flat subscription + service fee + at-cost compute. |
| `README.md` | This file. |

Total payload is lean (all four served files are well under a few hundred KB
combined; the demo page is self-contained and < 150 KB).

## Run locally

No build, no dependencies. Any static file server works:

```bash
# from the repo root
python3 -m http.server -d site 8080
# then open http://localhost:8080/
```

Or just open `site/index.html` directly in a browser (all links and assets are
relative and local, so `file://` works too).

## Conventions kept from the product

- **Offline invariant:** no webfonts, no CDNs, no network calls. Fonts are system
  stacks (`-apple-system`/`Inter`/`Segoe UI` sans, `Iowan Old Style`/`Palatino`/
  `Georgia` serif, `ui-monospace` mono). The paper grain is an inline SVG data-URI.
- **Security posture:** the demo builds all DOM via `createElement`/`textContent`
  (no `innerHTML` carrying data), mirroring the app's test-enforced rule.
- **Calm motion:** short ease-out transitions, no bounce/overshoot; `prefers-
  reduced-motion` is honored (transitions/animations disabled).
- **Accessibility:** AA contrast throughout; the email form and segmented controls
  have labels/roles and live regions.

## Deploy to Cloudflare Pages

Per `docs/CLOUDFLARE_PLAN.md`, the SPA and any static surface drop straight onto
**Cloudflare Pages** (global CDN, custom domain). This `site/` directory is a
pure static bundle, so it's the simplest possible Pages deploy.

### Option A — dashboard (Git integration)
1. Push the repo to GitHub/GitLab.
2. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**.
3. Pick the repo. Build settings:
   - **Framework preset:** None
   - **Build command:** *(leave empty — no build)*
   - **Build output directory:** `site`
4. Deploy. Add a custom domain under **Custom domains** when ready.

### Option B — Wrangler (direct upload, no Git)
```bash
npm i -g wrangler            # or: npx wrangler ...
wrangler pages deploy site --project-name ontoforge-site
```

This uploads `site/` as-is. No `wrangler.toml` is required for a plain static
Pages project.

## Wiring the email-capture stub to a real backend

`index.html#signup` currently validates the email client-side and shows a
thank-you — **nothing is sent**. To capture for real, add a Cloudflare **Pages
Function** and point the form at it. The hook point is marked in `index.html`
with a `BACKEND HOOK` comment.

1. Create `site/functions/api/signup.js`:
   ```js
   export async function onRequestPost({ request, env }) {
     const { email } = await request.json();
     if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email || "")) {
       return new Response(JSON.stringify({ ok: false }), { status: 400 });
     }
     // Persist however you like, e.g.:
     //   await env.SIGNUPS.put(email, new Date().toISOString());   // KV binding
     //   or send via Resend / Cloudflare Email Routing
     return new Response(JSON.stringify({ ok: true }), {
       headers: { "content-type": "application/json" },
     });
   }
   ```
   Pages auto-discovers `functions/` and serves it at `/api/signup` — no extra
   config. Bind a KV namespace (`SIGNUPS`) in the Pages project settings if you
   use the KV line.

2. In `index.html`, replace the stub's "thank-you" branch with a real `fetch`:
   ```js
   const res = await fetch("/api/signup", {
     method: "POST",
     headers: { "content-type": "application/json" },
     body: JSON.stringify({ email: v }),
   });
   if (res.ok) { /* show ok message */ } else { /* show error */ }
   ```

> Note: a real signup form should add a bot guard (Cloudflare **Turnstile**) and
> rate-limiting before going live. Keep the offline/no-CDN posture for the
> marketing pages themselves; the Function runs server-side on Pages.

## Notes for maintainers

- All copy leads with **granularity** (value-level lineage), backed by **calibrated
  abstention** ("never confidently wrong"), the **client-side anonymization** trust
  story ("we never see your raw data"), the **$0-exit** guarantee, and
  **compute-at-cost** economics. This is the deliberate positioning order from
  `docs/STRATEGY_MEMO.md` §3 and `docs/MARKET_EDGE.md`.
- The proof numbers on the landing page (0 confidently-wrong, 100% citation
  coverage, ECE ≤ 0.05, 100% AMBER replay, ER F1 0.997, conformal within 1.45%)
  come from the repo's measured gate suite (README "Measured results"). They are
  **fixture-scale, deterministic, zero-network** — keep that caveat in the footer.
- This site touches **no `src/` code** — the engine test suite is unaffected.
