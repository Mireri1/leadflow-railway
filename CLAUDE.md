# LeadFlow — Project Context

## What This Is
Cold calling CRM for Vision's sales team. Reps log in, see leads, dial via Google Voice, log call outcomes with qualification data, and track performance. Eric (admin) manages quotas, monitors callers, and handles turnover.

## Tech Stack
- **Frontend**: React + Vite (single-file SPA: `frontend/src/App.jsx`)
- **Backend**: Python FastAPI (`backend/main.py`)
- **Database**: Supabase (project: ucpwpjokyconwzwqvdad)
- **Hosting**: Railway (Dockerfile builds both frontend + backend)
- **Production URL**: https://leadflow-railway-production.up.railway.app

## Architecture

### Single-File Frontend
The entire UI is in `frontend/src/App.jsx` (~2600 lines). No component library, no Tailwind — all inline styles with a CSS string injection pattern. Dark navy theme (#060e20 background).

### Auth System
- Shared team password: `TEAM_PASSWORD` env var (default: LeadFlow2024)
- Admin password: `ADMIN_PASSWORD` env var (default: LF@dmin2024!Mx)
- Admin users: `ADMIN_USERS` env var (default: eric)
- Blocked users: `BLOCKED_USERS` env var (comma-separated, persisted per deploy)
- Runtime blocking via `POST /api/auth/block` (resets on deploy)
- JWT tokens include role: `admin` or `caller`
- Login logging to `login_log` table (success/failed/blocked)

### Call Logging Flow (Two-Step)
1. **Step 1 — What happened?** No Answer / Voicemail / Answered
2. **Step 2 — If Answered:** Not Interested / Interested / Callback / Converted
3. **Callback shows:** reason picker (DM Unavailable, Requested Later, Needs Approval, Timing, Gatekeeper, Other) + date
4. **Qualification required** for: Interested, Converted, Callback (fields: budgetfocus, vendorstatus, decisionmaker, timeline, qualified)
5. **Auto-timer** starts when modal opens, captures duration automatically

### Key Tables (Supabase)
- `leads` — prospects with score, status, assignedTo, callbackDate
- `call_outcomes` — every call logged (outcome, duration, qual fields, calledBy)
- `scripts` — call scripts by industry
- `app_settings` — key/value store for quotas (daily_quota, quota_<username>)
- `login_log` — login audit trail

### Column Names Are LOWERCASE
Supabase columns: `budgetfocus`, `vendorstatus`, `decisionmaker`, `timeline`, `qualified`, `followupsequence`, `nextfollowup`, `followupstep`. The frontend sends lowercase keys.

## Features

### Caller Features
- Lead list with search, filters (status, industry, state), pagination
- Dialer mode — focused one-lead-at-a-time view, only unclaimed leads
- Call modal with two-step outcome flow + auto-timer
- Qualification enforcement on engagement outcomes
- Personal daily quota progress bar
- Notification bell (overdue/due today/tomorrow follow-ups)
- Future Follow-Ups tab (6+ months out)
- Qualified Leads tab (calls with qual data)
- History with date range + rep filters

### Admin Features (Eric only)
- Team Management: rep status (active/idle/inactive), Set Quota per caller, Reassign, Release to Pool, Block
- Login Activity panel (collapsible, shows all login attempts)
- Leaderboard with contact rate, avg talk time, first call vs follow-up split, anti-gaming flags
- CSV export on History
- Recycle Stale button (unassign leads untouched 7+ days)
- Per-caller quotas via `PUT /api/quota` with `caller` field

### Anti-Gaming
- Empty form flag (no notes + no qual data)
- Duplicate cooldown (same lead + same rep within 5 minutes)
- Rapid cadence (5+ calls in 5 minutes)
- Leaderboard flags: >50% conv rate, >95% contact rate (admin-visible only)
- Flags stored in `follow_up_outcome` field on call_outcomes

### Follow-Up Sequences
- Hot Lead: 24h → 48h → 5 days
- Standard: 48h → 5 days → 7 days
- Slow Burn: 48h → 7 days → 14 days
- Long Nurture: 30 → 60 → 90 days
- Future: 3 months → 6 months
- Far Future: 6 months → 1 year

## Critical Rules

### Route Ordering
- `/api/calls/qualified` MUST be defined BEFORE `/api/calls/{lead_id}` in main.py
- `/api/calls/history` MUST be defined BEFORE `/api/calls/{lead_id}`
- FastAPI matches routes in order — wildcard catches everything if first

### React Hooks
- Never use `useState`/`useEffect` inside IIFEs or conditionals
- Extract to proper components (e.g., `LoginActivityPanel`)
- The Future Follow-Ups IIFE is safe (no hooks, just computed values)

### Supabase CHECK Constraints
- `call_outcomes` does NOT have a `contract_value` column — never send it
- Send only columns that exist on the table or Supabase rejects the entire insert

## Key Files
- `frontend/src/App.jsx` — entire UI (single file)
- `backend/main.py` — entire API (single file)
- `Dockerfile` — builds frontend then backend, serves via uvicorn

## Nightly Health Check

Run this diagnostic daily to catch issues. Auto-fix what you can.

### LeadFlow Checks
1. Login as admin: `POST /api/auth/login` with `{"username":"Eric","password":"LF@dmin2024!Mx"}`
2. `GET /api/stats` with auth token — verify returns data with `total` field
3. `GET /api/leaderboard` — verify returns array
4. `GET /api/calls/qualified` — verify returns array (not error)
5. `GET /api/quota` — verify returns `quota` field
6. `GET /api/calls/history` — verify returns `calls` array

### Supabase Direct Access
- URL: `https://ucpwpjokyconwzwqvdad.supabase.co`
- Use service role key from LeadFlow's Supabase (in HQ's `.env.local` as `LEADFLOW_SUPABASE_KEY`)
- Table: `call_outcomes` (NOT `calls`), column: `calledAt` (NOT `created_at`)

### Auto-Fix Rules
- API returns error → investigate code, fix, build (`cd frontend && npm run build`), commit, push
- Route ordering issues → `/api/calls/qualified` must be BEFORE `/api/calls/{lead_id}` in main.py
- Login fails → check ADMIN_PASSWORD env var in Railway

### Report Format
```
LEADFLOW:
- API: OK/FAIL
- Stats: OK/FAIL
- Leaderboard: OK/FAIL
- Qualified: OK/FAIL
- Quota: OK/FAIL
ACTIONS TAKEN: [list or "None needed"]
```

## Environment Variables (Railway)
- `SECRET_KEY` — JWT signing key
- `TEAM_PASSWORD` — shared caller password
- `ADMIN_PASSWORD` — Eric's admin password
- `ADMIN_USERS` — comma-separated admin usernames (default: eric)
- `BLOCKED_USERS` — comma-separated blocked usernames
- `DAILY_CALL_QUOTA` — default quota if not in app_settings (default: 60)
- `SUPABASE_URL`, `SUPABASE_KEY`
- `GOOGLE_API_KEY` — Google Places for lead finding
- `ANTHROPIC_API_KEY` — powers the note assistant + AI insights. If unset, both fall back to a keyword heuristic — never hard-fails.
- `HAIKU_MODEL` — per-note assistant model (default: claude-haiku-4-5-20251001). High-frequency, simple classification → Haiku.
- `INSIGHTS_MODEL` — pattern-insights model (default: claude-sonnet-4-6). Low-frequency, multi-note synthesis → Sonnet.
- `WEEKLY_DIGEST_ENABLED` — auto weekly AI review to Slack (default: 1)
- `WEEKLY_DIGEST_DAY` — weekday to send (0=Mon … 6=Sun, default: 0)

## Note Intelligence (Haiku)
- `POST /api/notes/analyze` `{note, company?, status?}` → `{sentiment: warm|neutral|cold, outcome, callbackDate (ISO, parsed from plain English), summary, engine}`.
- Sentiment is persisted in the lead's `notes` as a `[sent:warm|neutral|cold]` tag (same tokenized-notes pattern as `[INTENT:*]` — no schema change). `cleanNote()` strips it; `parseSentiment()` reads it; `<SentimentDot/>` renders the colored dot.
- Caller UI: CallModal + My Week have 🎤 Dictate (Web Speech API) and ✨ Smart-fill (applies suggested outcome + callback date in one tap).
- `POST /api/analytics/note-insights?days=N` (admin) → Sonnet reads recent notes (call_outcomes + leads incl. imported lists/My Week) → `{headline, objections[], timing[], segments[], opportunities[], watchouts[], engine, sample, cached}`. 30-min cache; `refresh=1` bypasses. `generate_note_insights()` + `_gather_note_records()`.
- **Receptivity Index** (`GET /api/analytics/receptivity?days=N`, admin): composite of contact-rate + engagement-rate (0–100) — dense signal so slices stay significant despite the ~0.2% close rate. Returns `by_industry / by_dow / by_hour / by_month / by_industry_month` + `overall`. `_agg_recept()`, CONTACT_OUTCOMES/ENGAGED_OUTCOMES. ReceptivityPanel in Analytics.
- **Macro backdrop** (`GET /api/analytics/macro`, admin): live FRED series via the public CSV export (`fredgraph.csv?id=…`, NO API key) — UNRATE/FEDFUNDS/DGS10/PAYEMS/UMCSENT. `run_macro_snapshot_if_due()` banks one snapshot/month into `app_settings` (`macro_snapshot_YYYY-MM`) so a paired macro×receptivity history accumulates for later correlation (Phase 3). `_fetch_fred_latest()`, `get_macro_snapshot()`. Env: RECEPTIVITY_TZ_OFFSET_HOURS (default -4), RECEPTIVITY_MIN_SLICE (15).
- Weekly Slack review: `GET /api/weekly-summary` posts the full Sonnet analysis + week-over-week metrics. Auto-fires once per ISO week (on/after `WEEKLY_DIGEST_DAY`) from `_bg_maintenance_loop` via `run_weekly_digest_if_due()` (cooldown row `last_weekly_digest`). The daily digest stays lean (keyword themes + trend only).
