# daily_brief v0.4 — Browser-first

**Status:** Proposed
**Date:** 2026-05-24
**Author:** Hale (with Claude Code)
**Scope:** small (one engineer, one evening)

---

## Why this version exists

daily_brief was built as a morning-email habit. The thesis was: a push notification at 9am pulls the developer back into the project. That works, but email has three structural problems for this use case:

1. **Ephemeral.** Once read, a brief is buried under newer mail by the next morning. There's no skim-back-to-Tuesday motion.
2. **Search-poor.** Gmail search on technical content is unreliable. *"What was the spotlight on the RLS work three days ago?"* is hard to recover.
3. **No comparison surface.** Looking at two days' briefs side by side, or scrolling through a week, requires juggling email tabs.

Meanwhile, daily_brief has been quietly writing perfectly good HTML to `.brief/{date}.html` since v0.1. The archive is right there. The user just can't reach it without remembering the filename and typing it into a browser bar.

**v0.4 makes the local HTML archive the primary surface. Email becomes opt-in.**

---

## The thesis, in one sentence

*The artifact daily_brief produces is more valuable than the notification it sends — so make the artifact bookmarkable and the notification optional.*

---

## What changes

### 1. Per-day JSON sidecar

Every successful brief render writes a metadata file alongside the HTML:

```
.brief/2026-05-24.html   ← the brief itself (unchanged)
.brief/2026-05-24.json   ← new
```

Contents:

```json
{
  "date": "2026-05-24",
  "headline": "RLS enforcement at the connection layer",
  "spotlight_title": "Middleware: set_rls_context()",
  "spotlight_label": "API middleware"
}
```

**Why a sidecar instead of parsing the HTML or appending to a rolling JSON file:**

- HTML parsing is fragile across rendering changes.
- A rolling file (e.g., `index.json`) creates read-modify-write race conditions and merge headaches.
- Per-day sidecars are atomic, self-contained, and trivially globbable.

### 2. `.brief/latest.html`

A stable bookmark target that always shows today's brief. Implementation: a meta-refresh redirect, regenerated on every run.

```html
<!DOCTYPE html>
<meta http-equiv="refresh" content="0; url=2026-05-24.html">
```

**Why meta-refresh and not a symlink or a copy:**

- Symlinks are FS-fragile, render oddly in some viewers, and break under sync tools.
- A copy doubles disk usage and gets out of sync if the writer is interrupted.
- A four-line HTML redirect is the simplest, most portable thing that works.

### 3. `.brief/index.html`

Globs every `*.json` sidecar in `.brief/`, sorts newest-first, renders a list page. Each row shows date, headline, and a link to the brief. Becomes the primary bookmark.

### 4. Cron behavior change

The launchd job (`com.daily-brief.plist`) currently runs with `--no-open --email`. v0.4 drops `--email` from the cron invocation.

- **Default cron path:** `daily-brief --project <path> --no-open`. Writes the brief, JSON, latest, and index. No email.
- **Manual email:** `daily-brief --email` still works for the days you actually want a push.

---

## What stays the same

Everything that's working today. Specifically:

- Brief generation pipeline (git activity gathering, spotlight selection, Claude call, HTML render) — untouched.
- Coverage rotation logic — untouched.
- `.daily-brief.yaml` schema — untouched.
- `--email` and `--no-open` flag semantics — preserved.
- Per-day HTML filename and location — unchanged.

v0.4 is purely additive on the output side and one flag-flip on the cron side. No breaking changes to existing briefs or configs.

---

## Bookmark experience after v0.4

Two URLs to memorize:

- `file:///…/.brief/latest.html` → today's brief, always.
- `file:///…/.brief/index.html` → full archive, newest first.

These are local files. No hosting, no auth, no privacy concerns about project content leaving the laptop. The archive is grep-able from the filesystem and version-controllable per-project if any project wants to.

---

## What this isn't

Naming the boundary so v0.5 doesn't drift:

| daily_brief is | daily_brief isn't |
|---|---|
| Morning context restoration for a developer | A CI report (no test results, no build status) |
| Single-author, single-project | A team standup substitute |
| Educational — "explain your choices" | A code review tool (no quality or security judgments) |
| Lightweight (~$0.10/day, one API call) | A full project-history tool — coverage rotation is local heuristic, not search |

The "interview prep disguised as a morning standup" framing in the README is about *content quality* — explain your choices like you would to a hiring manager — not about Q&A generation.

---

## Acceptance criteria

- [ ] `.brief/{date}.json` written after every successful brief render.
- [ ] `.brief/latest.html` regenerated to point at today's brief at the end of every run.
- [ ] `.brief/index.html` regenerated listing all briefs newest-first; renders cleanly even when some old briefs lack a JSON sidecar (degrades to date-only entry).
- [ ] Cron runs with `--no-open` and no implicit `--email`.
- [ ] Manual `daily-brief --email` still works end-to-end.
- [ ] README updated to describe the browser-first flow as the primary path.

---

## Out of scope for v0.4

Deferred deliberately, captured here so they don't get lost:

- **Search across briefs.** Useful, requires JS in the index page. v0.5.
- **Cross-project index.** "Show me yesterday across moon_baby + daily_brief + product-page." Useful once Hale has 3+ active projects on the brief habit. v0.5+.
- **Tag/filter by spotlight area** in the index. Nice-to-have. v0.5+.
- **Backfilling JSON sidecars** for existing briefs. One-time migration; will be a small `--backfill-index` flag or a separate script, not part of v0.4 default flow.

---

## Open questions

Resolved during planning (2026-05-24):

| Question | Resolution |
|---|---|
| `latest.html` mechanism — symlink, copy, or redirect? | Meta-refresh redirect. |
| Index metadata source — parse HTML, sidecar, or rolling JSON? | Per-day JSON sidecar. |
| Cron behavior — keep email default or drop it? | Drop `--email` from the cron; browser-first. |
| PRD before or after code? | PRD first. (This doc.) |

Nothing currently open. If anything surfaces during implementation, it gets added here and re-resolved before the code lands.

---

## Why this PRD is also rendered as HTML

Independent experiment running alongside this work: **does an HTML-rendered PRD outperform a markdown one for both human readability and Claude Code comprehension?**

The markdown version (this file) is the source of truth. The HTML version (`prd.html`) is the same content, styled. After v0.4 lands, both will exist in `docs/`, and we can run the test: which one does the next reader actually read?

Findings — if interesting — get sent as feedback to the [Context Bridge](https://github.com/contextbridge/planbridge) project, since the HTML-PRD pattern is something their tool could meaningfully engage with.
