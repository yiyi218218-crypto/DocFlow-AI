# DocFlow AI — Part 4: Frontend Architecture

> Section 14, plus the document viewer deep-dive (your "explain how to technically implement this reliably" question).

---

## 14. Frontend Architecture

### 14.1 Stack and the reasoning behind each choice

| Layer | Choice | Why | Rejected |
|---|---|---|---|
| Framework | **Next.js 15, App Router** | RSC for the shell (fast first paint, no client-side auth flash), file routing, Vercel deploys | Vite SPA — worse SEO for the marketing pages, manual routing; Remix — smaller ecosystem |
| Language | **TypeScript, `strict`** + `noUncheckedIndexedAccess` | The API client is generated from OpenAPI; strictness is what makes that pay off | — |
| Styling | **Tailwind v4** | v4's CSS-first config removes `tailwind.config.js` sprawl; design tokens live as CSS variables | CSS Modules (no design-system discipline); styled-components (runtime cost, RSC friction) |
| Components | **shadcn/ui** | You own the code — no version-lock, fully restyleable | MUI/Chakra (heavy, opinionated, fights Tailwind); Radix raw (you'd rebuild shadcn) |
| Server state | **TanStack Query v5** | Caching, dedup, background refetch, optimistic updates, retry — all things you'd write badly by hand | SWR (thinner mutation story); RSC-only (no client cache for a polling-heavy app) |
| Client state | **Zustand — exactly two stores** | See §14.3 | Redux (ceremony); Context (re-render storms in the viewer) |
| URL state | **nuqs** | Filters, sort, page, active document — all shareable and back-button correct | Manual `useSearchParams` juggling |
| Forms | **react-hook-form + zod** | Uncontrolled inputs = no re-render per keystroke; zod schemas mirror Pydantic | Formik (slower, older) |
| PDF | **`pdfjs-dist` directly** | You need custom overlays and text-layer access; a wrapper gets in the way | `react-pdf` — convenient but lags pdfjs releases and hides the APIs you need |
| Tables | **TanStack Table** | Headless; sorting/filtering/virtualization without visual opinions | AG Grid (heavy, licensed) |
| Dates | **`date-fns` + `@date-fns/tz`** | Tree-shakeable, immutable | Moment (dead); Luxon (bigger) |
| Charts | **Recharts** | Only for the usage page; small surface | — |
| Palette | **`cmdk`** | ⌘K is table stakes for a Linear-inspired product | — |
| Tests | **Vitest + Testing Library + Playwright + MSW** | | Jest (slower with ESM) |

### 14.2 Route structure

```
app/
├── (marketing)/
│   ├── page.tsx                     Landing — RSC, static, no JS beyond the nav
│   ├── demo/page.tsx                Live demo, no auth, pre-processed fixtures
│   └── pricing|privacy|terms/
├── (auth)/
│   ├── login | register | verify-email | forgot-password | reset-password
│   └── layout.tsx                   Split layout, redirects if already authed
├── (app)/
│   ├── layout.tsx                   RSC: session, workspace, sidebar, cmd palette
│   ├── page.tsx                     Dashboard
│   ├── documents/
│   │   ├── page.tsx                 Library (grid/list, filters in URL)
│   │   └── [id]/
│   │       ├── page.tsx             THE VIEWER — split pane
│   │       └── loading.tsx          Skeleton matching the final layout
│   ├── tasks | calendar | search | chat
│   └── settings/(profile|workspace|members|usage|notifications)
├── api/                             Route handlers — thin proxy only
│   ├── auth/[...]/route.ts          cookie ↔ header bridge
│   └── proxy/[...path]/route.ts     SSR fetches that need the refresh cookie
└── globals.css                      Tailwind v4 @theme tokens
```

**Route groups carry different layouts and different auth postures**, which is exactly what they are for. `(marketing)` ships almost no JS. `(app)` is authenticated and client-heavy.

### 14.3 State: the discipline

**Server state → TanStack Query. Always. No exceptions.**

```
queryKeys = {
  documents: {
    all:      ['documents'],
    list:     (f) => ['documents','list', f],
    detail:   (id) => ['documents','detail', id],
    analysis: (id) => ['documents','analysis', id],
  },
  tasks: { ... }, deadlines: { ... },
}
```
A single `queryKeys` factory. Ad-hoc key arrays scattered through components make invalidation guesswork, and cache bugs are the most confusing class of frontend bug.

**Zustand — exactly two stores, both justified:**

1. **`viewerStore`** — `currentPage`, `zoom`, `activeHighlight`, `pageRects`, `scrollTarget`, `sidebarTab`. This is genuinely global, updates at high frequency (scroll), and is read by three sibling component trees. Context here causes re-render storms across the whole viewer on every scroll tick.
2. **`uploadStore`** — the upload queue with per-file progress. Must survive navigation away from the upload page; a user who navigates to a document mid-upload must not lose the upload.

**Everything else is URL state (nuqs), form state (RHF), or local `useState`.** If you find yourself adding a third store, the state probably belongs in the URL.

**Polling with backoff (MVP), then SSE (Phase 2):**
```
refetchInterval: (query) => {
  const s = query.state.data?.status
  if (s === 'completed' || s === 'failed') return false
  const elapsed = Date.now() - startedAt
  return elapsed < 30_000 ? 2000 : elapsed < 120_000 ? 5000 : 15_000
}
```
Fifteen lines, no infra. Replace with SSE when you have >100 concurrent processing documents or the poll cost shows up in metrics.

### 14.4 Design system

**Visual direction: Linear's density and keyboard-first discipline, Notion's content calm.**

```css
@theme {
  /* Neutral-first. One accent. No gradients. */
  --color-bg:        oklch(0.99 0 0);
  --color-surface:   oklch(1    0 0);
  --color-border:    oklch(0.92 0.004 260);
  --color-fg:        oklch(0.22 0.01  260);
  --color-muted:     oklch(0.55 0.01  260);
  --color-accent:    oklch(0.58 0.19  264);   /* indigo — used sparingly */

  --color-confidence-high:   oklch(0.62 0.15 150);
  --color-confidence-medium: oklch(0.72 0.15  75);
  --color-confidence-low:    oklch(0.60 0.02 260);
  --color-highlight:         oklch(0.88 0.16  95 / 0.42);

  --font-sans: "Inter Variable", system-ui, sans-serif;
  --font-bangla: "Noto Sans Bengali", var(--font-sans);
  --font-mono: "JetBrains Mono", ui-monospace;

  --radius: 0.5rem;
  --shadow-sm: 0 1px 2px oklch(0 0 0 / 0.05);
}
```

**Rules that keep it from becoming generic AI slop:**
- **Borders, not shadows.** A 1px border at 8% opacity reads as precise; a drop shadow reads as a template.
- **Zero gradients.** Not on buttons, not on hero sections, not on cards. This alone separates the UI from 90% of AI products.
- **One accent color**, used for primary actions and active states only. Everything else is neutral.
- **Density.** 32px row heights, 12–14px body text in the app shell. Users scanning 40 documents want density, not airy marketing spacing.
- **Motion is functional only.** 120–180 ms ease-out on state changes. The one deliberate flourish: the citation highlight pulse (§14.5), because it teaches the user what just happened.
- **`prefers-reduced-motion`** disables all of it.
- **Bangla typography needs real attention** — Noto Sans Bengali, `line-height: 1.75` (conjuncts need vertical room), and `lang="bn"` on containers so the browser picks the right font and shaping.
- **Empty states are designed, not afterthoughts.** "No documents yet" gets an illustration, a one-line explanation, and the upload CTA.
- **Dark mode from day one** via CSS variable swap under `.dark` — retrofitting is significantly harder.

### 14.5 The Document Viewer — the hard part, done properly

This is the feature that makes the demo land. It has to be exactly right.

#### Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│ ← Circular_2026.pdf   ·   University Circular · Bangla · 4 pages     │
├──────────────────────────────────┬──────────────────────────────────┤
│                                  │ [Insights] [Tasks] [Chat] [Q&A]  │
│      PDF CANVAS                  │                                  │
│      (virtualized pages)         │  ⏰ Deadlines                    │
│                                  │  ┌────────────────────────────┐  │
│   ┌──────────────────────┐       │  │ Application submission     │  │
│   │                      │       │  │ 15 September 2026 · 5:00PM │  │
│   │   ▓▓▓▓▓▓▓▓▓▓▓▓▓      │◄──────┼──│ ● high  📄 Page 3     ↗    │  │
│   │   highlighted span   │       │  └────────────────────────────┘  │
│   │                      │       │                                  │
│   └──────────────────────┘       │  📋 Required Documents (4)       │
│                                  │  ○ Academic transcript   p.2     │
│  [−] 100% [+]  ⛶   ‹ 3/4 ›       │  ○ NID copy              p.2     │
└──────────────────────────────────┴──────────────────────────────────┘
   Resizable split (persisted) · stacks vertically below 1024px
```

#### The coordinate pipeline — where reliability comes from

The whole feature reduces to one question: **how does a rectangle computed in Python end up in the right place on a canvas in the browser?**

```
BACKEND (Python)
  PyMuPDF page.get_text("words")
    → [(x0,y0,x1,y1, word, block, line, word_no), ...] in UNROTATED PDF space
  Apply page.rotation_matrix                       ← the step everyone forgets
  Normalize against page.rect (the DISPLAY box, post-/Rotate):
    nx0 = x0 / page.rect.width,  ny0 = y0 / page.rect.height
  Store in document_pages.text_map:
    { "text": "...", "words": [[cs, ce, nx0, ny0, nx1, ny1], ...] }

  Fact alignment (§11.5) → char span → overlapping words → group by line
    → union per line → source_rects: [{page, x0, y0, x1, y1}] all in [0,1]

FRONTEND (TypeScript)
  const viewport = page.getViewport({ scale, rotation: 0 })  // pdfjs applies /Rotate itself
  left   = rect.x0 * viewport.width
  top    = rect.y0 * viewport.height
  width  = (rect.x1 - rect.x0) * viewport.width
  height = (rect.y1 - rect.y0) * viewport.height
```

**Why normalize to [0,1] on the backend:** the browser renders at arbitrary zoom, device pixel ratio, and container width. Any absolute unit — points, pixels, DPI-dependent values — requires the frontend to know the backend's rendering assumptions. Normalized coordinates are scale-, zoom-, and DPR-invariant. **One multiplication in the browser, no shared assumptions, no drift.**

**Why apply rotation on the backend:** `page.rect` reflects `/Rotate` while word boxes from `get_text` do not. If you skip `rotation_matrix`, highlights on rotated pages (extremely common in scanned documents) land at 90° offsets. pdf.js applies `/Rotate` when building the viewport, so the two sides agree only if the backend has already normalized against `page.rect`.

> **Write a unit test with a fixture containing 0°, 90°, 180°, and 270° pages.** This bug is invisible in development and obvious to users.

#### Rendering strategy

**Canvas + absolutely-positioned overlay divs — not SVG, not the pdf.js text layer for highlights.**

```
<div class="page-container" style="width:{vw}px; height:{vh}px; position:relative">
  <canvas />                          <!-- pdf.js render -->
  <div class="text-layer" />          <!-- pdf.js text layer: selection & Ctrl+F only -->
  <div class="highlight-layer">       <!-- ours, pointer-events:none -->
    {rects.map(r => <div class="hl" style={{left, top, width, height}} />)}
  </div>
</div>
```

*Rejected: using the pdf.js text layer's own spans for highlighting.* They are positioned per text-run for selection purposes, do not align to arbitrary character spans, and change between pdf.js versions. Coupling your product's core feature to an internal implementation detail of a third-party renderer is a bad trade.

*Rejected: SVG overlay.* No benefit for axis-aligned rectangles; divs are cheaper and easier to animate with CSS.

#### Virtualization

100-page documents must not render 100 canvases. `IntersectionObserver` on page containers with `rootMargin: '1200px'`:
- **Visible ±1 page:** rendered canvas
- **±2–5 pages:** cached WebP page render from `document_pages.storage_key_image` (instant, cheap)
- **Beyond:** an empty div at the correct height (from stored page dimensions) so the scrollbar is always accurate

Page heights come from the database, so the scroll container's total height is correct before anything renders. No layout shift while scrolling — the single most annoying failure mode of naive PDF viewers.

#### The citation click flow

```
1. User clicks a fact card in the right pane
2. viewerStore.setActiveHighlight({ page: 3, rects: [...], factId })
3. If page 3 is not rendered → force-render it, await the promise
4. scrollTarget effect: pageEl.scrollIntoView({ behavior:'smooth', block:'center' })
     — but if the jump is > 3 pages, use behavior:'auto' (instant).
       Smooth-scrolling 40 pages takes seconds and feels broken.
5. Highlight mounts with a 900ms pulse:
     0%   background: highlight/0.75, outline 2px accent
     100% background: highlight/0.42, outline transparent
6. Fact card gains an active ring; both stay linked until another is selected
7. URL updates: ?fact=<id>&page=3   ← shareable, back-button correct, E2E-testable
```

**Step 7 matters more than it looks.** Putting the active fact in the URL makes the highlight state shareable ("look at this deadline"), survivable across refresh, and — critically — directly assertable in a Playwright test.

**Reverse direction:** while scrolling the PDF, the fact list highlights facts on the visible page. Two-way linking is what makes the panes feel like one object rather than two.

#### Degradation, handled honestly

| Situation | Behavior |
|---|---|
| `citation_status = 'unverified'` | Card shows "Source not verified"; clicking navigates to the page with **no** highlight |
| `citation_granularity = 'page'` (VLM OCR) | Navigate to the page, flash a subtle full-page border, no span highlight |
| Rects present but page failed to render | Show the cached WebP with the overlay on top (coordinates are identical — this is the payoff for normalizing) |
| PDF fails entirely (corrupt) | Fall back to a paged text view built from `document_pages.text`, with character-offset highlighting via `<mark>` |

**Never fake a highlight.** A confidently wrong highlight destroys more trust than an absent one, because it teaches the user the system lies.

### 14.6 Screen-by-screen intent

| Screen | The one thing it must do | Key detail |
|---|---|---|
| **Landing** | Show the citation click-through in under 10 seconds | Autoplaying muted video/GIF of the highlight interaction above the fold. Not a feature grid. |
| **Demo** (no auth) | Deliver the "oh." moment before the signup wall | Pre-processed fixtures, zero latency. Also the E2E fixture and the hackathon demo. |
| **Login/Signup** | Get out of the way | Google OAuth primary, email secondary. Two fields. No marketing copy. |
| **Dashboard** | Answer "what needs my attention?" | Top strip: next 14 days of deadlines. Then recent documents, then a *small* stats row. Not a chart wall. |
| **Upload** | Feel instant | Drag-drop anywhere in the app shell. Client-side validation and SHA-256 in a Web Worker before the request. Optimistic card appears immediately. |
| **Processing** | Make 90 seconds tolerable | Honest per-stage progress with real stage names. Show the page thumbnail as soon as normalization completes — visible progress beats a spinner. |
| **Document detail** | The split viewer | §14.5 |
| **Tasks** | Fast triage | Keyboard-first: `j/k` navigate, `x` complete, `e` edit. Group by due date. Every AI task shows its source document as a chip. |
| **Calendar** | Spatial deadline awareness | Month + agenda. Click → task → source fact → source page. The provenance chain holds all the way through. |
| **Search** | Prove the hybrid retrieval works | Result cards show the matching snippet, page, section path, and (in a dev toggle) per-retriever scores. |
| **Chat** | Grounded answers with visible sources | Citations render as inline pills; hovering previews the quote; clicking opens the viewer at that page. |
| **Members** | Invite in one action | Email + role, done. |
| **Usage** | Make cost legible | Documents, pages, tokens, cost, quota headroom. This page quietly demonstrates the `usage_records` architecture. |
| **Settings** | Boring and complete | Profile, timezone (drives reminders), notification prefs, workspace, danger zone. |

### 14.7 Performance budget

| Metric | Target | How |
|---|---|---|
| LCP (landing) | < 1.2 s | RSC, no client JS, `next/font`, static |
| LCP (dashboard) | < 1.8 s | RSC shell + streaming Suspense boundaries |
| First page painted (viewer) | < 800 ms | Cached WebP render first, canvas replaces it |
| Route JS (app) | < 180 kB gz | pdf.js dynamic-imported on the viewer route only |
| INP | < 200 ms | Virtualized lists, no state updates in scroll handlers |

**pdf.js is ~340 kB** and must never be in the shared bundle. `next/dynamic` with `ssr: false` on the viewer route only. The worker file must be copied to `/public` in the build step (or imported with `?url`) — Next.js will not bundle it correctly by default, and this will be your first viewer bug.

### 14.8 Accessibility

Not optional, and cheap if done from the start: keyboard reachability for every interaction (the viewer included — `n`/`p` for pages, arrow keys for fact navigation); visible focus rings, never `outline: none`; ARIA live region announcing processing stage changes; `aria-current` on the active fact; 4.5:1 contrast on all text including confidence badges (never encode confidence by color alone — the dot has a label); `lang` attributes for Bangla content; `prefers-reduced-motion` respected. Run `axe-core` in the Playwright suite so regressions fail CI.

---

*Next: [50-security-ops.md](50-security-ops.md) — threat model, observability, testing, CI/CD.*
