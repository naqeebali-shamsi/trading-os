# Multi-agent scratchpad (trading-os)

**Last updated:** 2026-05-15 (author: dashboard-bridge-status subagent)

## North star

Operator-grade autonomous trading stack; treat profit as empirical outcome after honest gates—not agent hype.

## Decided priorities (coordinator)

1. **Dashboard v0** — One view surfacing: bus tail, health/metrics, order/muscle state, mock vs live flags.
2. **Document/run entrypoints** — Keep canonical run notes here (see below); extend if scripts move.
3. **Known gaps checklist** — Track prior exploration items; do not fix unless trivial in the same edit as priority work.

## Dashboard v0 progress (this pass)

- Extended `consciousness/dashboard.py` into a dual-surface service:
  - API: `GET /api/state` (and legacy `GET /` kept as JSON for compatibility)
  - UI: `GET /ui`
  - Static assets: `GET /static/<asset>`
- Added dashboard data model with four required v0 blocks:
  - **System health/status:** `kernel/health.json` + strategy snapshot
  - **Telemetry summary:** best-effort read from `http://127.0.0.1:${TRADING_OS_TELEMETRY_PORT:-9876}` (`/health`, `/metrics`)
  - **Recent activity stream:** bus tail from `nervous/bus.jsonl`
  - **Mock-vs-live safety flags:** explicit effective mode + mock allowances + STOP_TRADING visibility
- Added minimalist retro UI (no gradients) using atomic design layers.

## Dashboard v1 progress (this pass)

- Added server-side event feed controls on `GET /api/state`:
  - `limit` query param with safe clamp (`1..200`, default `20`)
  - topic filters via `topics=market.*,muscle.*` and repeated `topic=` support
  - response now includes `event_feed` metadata (`limit`, `topic_filters`, `available_events`, `matched_events`)
- Extended UI feed controls in `consciousness/dashboard_ui/index.html` + `app.js`:
  - feed `limit` selector (`20/50/100`)
  - topic toggles (`market.*`, `muscle.*`, `immune.*`, `cortex.*`) wired to server-side filtering
  - stronger loading/error states via dashboard status banner and explicit unavailable copy
- Added route smoke tests in `tests/test_dashboard_routes.py`:
  - `/api/state` query handling (`limit`, topic filtering)
  - `/ui`, `/static/app.js`, `/api/state` basic route checks
  - static path traversal guard check (`/static/../dashboard.py` -> `404`)

## Dashboard bridge visibility progress (this pass)

- Added explicit bridge connectivity API route in `consciousness/dashboard.py`:
  - `GET /api/bridge/status`
  - optional query param `max_heartbeat_age` (float seconds, default `30.0`)
  - returns structured bridge health (`available`, `connected`, `mode`, root/chart heartbeat + tick details)
- Included bridge status inside aggregated state payload:
  - `GET /api/state` now includes `bridge_status` using the same probe path as `/api/bridge/status`
  - preserves existing `GET /` and `GET /api/state` compatibility
  - graceful fallback when bridge dependencies/probe are unavailable (explicit `mode: "unavailable"` instead of exceptions)
- Extended dashboard UI card stack:
  - new `Bridge Status` card in `consciousness/dashboard_ui/index.html`
  - new renderer in `consciousness/dashboard_ui/app.js` showing connection pills and root/chart heartbeat counters/details
  - added unavailable fallback copy for bridge card when API is down
- Extended smoke tests in `tests/test_dashboard_routes.py`:
  - `/api/bridge/status` JSON + `max_heartbeat_age` pass-through assertion
  - `/api/state` bridge status presence assertion
  - retained existing static/UI route sanity checks

## Dashboard atomic UI conventions

Location: `consciousness/dashboard_ui/`

- `foundations.css`: tokens/foundations only (colors, typography, spacing primitives, base body rules)
- `atoms.css`: smallest reusable styles (`atom-*` labels, titles, pills, inline code)
- `molecules.css`: grouped atoms (`molecule-card`, key-value list pattern)
- `organisms.css`: section-level composites (grid regions, feed list container/items)
- `templates.css`: page shell/template layout (header + content spacing)
- `index.html`: template/page composition of organisms for Dashboard v0
- `app.js`: lightweight render/update orchestration; no framework introduced

Conventions to preserve in next iterations:

- Keep class naming prefixed by layer (`atom-`, `molecule-`, `organism-`) to avoid style drift.
- Add new visual primitives to `foundations.css` first; avoid hard-coded ad hoc colors in deeper layers.
- Keep `app.js` focused on mapping `/api/state` to presentational blocks; put new backend fields in API state builder first.
- Maintain retro/minimal style constraints: flat colors, bordered panels, monospace, no gradients/shadows.

## Run / entrypoints (canonical notes)

- **Dashboard launch:** `python consciousness/dashboard.py`
- **Dashboard API:** `http://127.0.0.1:8765/api/state` (legacy JSON also available at `/`)
- **Dashboard UI:** `http://127.0.0.1:8765/ui`
- **Telemetry expected endpoint:** `http://127.0.0.1:9876` (`/health`, `/metrics`) if telemetry process is running
- **Ports:** `:8765` (dashboard) and `:9876` (telemetry)
- **Supervisor:** Use existing supervisor flow for stack bring-up/teardown; align dashboard v0 with whatever process owns health + bus consumers.

## Current repo understanding (brief)

- `trading-os/` holds bus (`nervous/`), supervisor, MT5 bridge + IPC + MCP, muscle orders, telemetry, consciousness JSON dashboard, mocks gated for live (LLM mock, TimesFM mock, calendar stub, etc.).

## Known gaps (checklist — not auto in-scope)

- [ ] Dashboard topic naming vs `muscle.order` (and related bus topics) — align UI labels/subscriptions with actual topic names.
- [ ] Cortex `open_positions` TODO (wire or document placeholder behavior).
- [ ] Sensory stubs — document what is stubbed vs live; avoid silent “healthy” when stubbed.

## Next agent instructions

- **TypeScript elsewhere:** Run `npm run lint` and `npm run typecheck` per project rules before PR/push; `npm run build` when emit matters.
- **Python in `trading-os/`:** Follow existing patterns and file layout; keep changes scoped.
- **Scope:** Do not expand beyond scratchpad priorities unless the user overrides.

## Commands run (this pass)

- `python -m py_compile consciousness/dashboard.py`
- `python -c "from consciousness.dashboard import _dashboard_state; s=_dashboard_state(); print(sorted(s.keys()))"`
- `python -m py_compile consciousness/dashboard.py && python -m pytest tests/test_dashboard_routes.py -q`
  - Result: exit code `0` in this environment (stdout was empty)
- `python -m py_compile consciousness/dashboard.py`
  - Result: exit code `0`
- `python -m pytest tests/test_dashboard_routes.py -q`
  - Result: exit code `0`

## Immediate next backlog (dashboard v1 shortlist)

- [x] Add simple `/api/state?limit=N` support for feed sizing without client-side truncation.
- [x] Add per-topic filter toggles in UI (`market.*`, `muscle.*`, `immune.*`, `cortex.*`).
- [ ] Wire richer order/muscle state into API from router state file when available.
- [x] Add smoke test for dashboard routes (`/api/state`, `/ui`, `/static/*`) to prevent regressions.
- [x] Add clearer unavailable-state copy when telemetry service is down.
- [x] Add explicit bridge status endpoint and bridge health card in dashboard UI.
- [ ] Add optional per-chart expansion UI if operators need chart-by-chart bridge diagnostics without leaving dashboard.

## Open questions for user

- Capital / risk tolerance.
- Target asset class.
- Paper vs live timeline.
