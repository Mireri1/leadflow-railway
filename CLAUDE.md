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

### Anti-Gaming / caller-data integrity (2026-09 rework)
- **`unsubstantiated_contact`** — a CONTACT outcome with no notes, no qual AND `duration <= UNSUBSTANTIATED_MAX_SEC` (10s). This is the real signal: measured over the 14,127-call history it hits 80% of one caller's claimed contacts vs 7% of another's.
- **`empty_form`** now fires ONLY on a contact outcome (no notes + no qual but a plausible duration). It used to fire regardless of outcome, so it hit every ordinary no-answer — **55% of all calls, 92% of one caller's**. A flag that fires on half the table is wallpaper; that is precisely why ~1,000 unverifiable rows went unnoticed for months. **Never widen it back to no_answer/voicemail** — blank notes on an unanswered ring is the CORRECT state. New rule fires on 8% of calls and never on a plain no-answer.
- Duplicate cooldown (same lead + same rep within 5 minutes); rapid cadence (5+ calls in 5 minutes).
- Leaderboard (admin-visible only): `contact_talk_median` = median duration over that caller's **claimed contacts** — 2s vs 32s across the Jun-8 switch, the single clearest integrity signal, and free because `duration` is already selected. Median not mean (mean was 11.3s vs 2s median — 4x more forgiving). Plus `substantiated_rate` / `unsubstantiated`, which only count calls logged since the flag shipped, so read them alongside `contact_talk_median`, which works retroactively. Flags: `high_conv_rate` (>50%), `perfect_contact` (>95%), `low_contact_talk_time`, `mostly_unsubstantiated` (both need ≥20 contacts).
- **Capture-time prompt** in CallModal: claiming a conversation with no note, no qual and ≤10s on the timer asks for one line before saving. A `confirm`, never a block — a genuine instant hangup is real, and blocking would push callers to mislabel it `no_answer`, recreating the exact data loss this branch removed.
- `duration` is MODAL-OPEN time, not carrier talk time, so a caller who dials separately and logs afterwards also reads near-zero. The flag therefore means **unverifiable**, not fabricated — keep the name honest.
- Flags stored in `follow_up_outcome` field on call_outcomes.

### Cold-call script (2026-10 rewrite — two steps, terse; don't soften it back)
- `buildOpener(lead)` in App.jsx is the script Cristine sees (CallModal card + "Opening line" box on the dialer card). The DB `scripts` table (Settings → Scripts) is a separate, optional per-industry overlay.
- **Step 1 — get past the front desk.** With a name on file: *"Hey, is [First] around?"* — nothing else, no company name, no reason; if asked, *"Just following up with them."* Without a name: *"Hey, quick question — who handles facilities over there?"* → get the name, thanks, hang up, log **Gatekeeper** with the name, ring back next day by name. **Never put the pitch or company name in step 1** — that is the receptionist's cue to screen, and it is exactly what the old "Hi, I won't take up much of your time… the one thing we specialize in…" opener did.
- **Step 2 — DM on the line:** *"Hey [Name], this is [Caller] with Vision Cleaning — we handle commercial cleaning for a few places in the area. Quick question: are you under contract with someone right now, or handling it in-house?"* Their answer IS the Vendor Status qual field; `QUALIFY_QUESTIONS` follow from it.
- Hot-intent lines (inspection / reviews / new build) are **ammo after the DM answers**, never the opener.
- **👤 Who's in charge (2026-10)** — the contact strip at the top of CallModal is where the name, title and **direct line** the script earns get stored. Name/title live on the existing `firstName`/`lastName`/`title` columns and ride the same lead PATCH as the outcome; the direct line is **`leads.dm_phone` (migration `009_leads_dm_phone.sql`, run it)** and goes in its **own PATCH** so a missing column can only fail the direct-line save — loudly (alert naming the migration) — never the call log or the name. `update_lead` now raises on a non-2xx from Supabase; it used to return the PostgREST error body with HTTP 200, so a rejected write looked saved. A newly-captured name is also stamped on the call note (`DM: <name> ·`) so History shows when it was learned. Gatekeeper retry prefill is `nextBusinessDay()` (was +3d); pressing 4 on a no-name lead focuses the Name field.
- **"Thursday at noon" callback chips (2026-10)** — `CallbackWhen` (gatekeeper panel + Callback panel) replaces the bare date input: day chips (Today / Tomorrow / Mon–Fri = **next** occurrence via `nextWeekday()`, strictly after today / Next week / In 2 weeks; weekend days marked ⚠) set `callbackDate`; time chips (`CALLBACK_TIMES`, or free text) set `cbTime`. **`callbackDate` stays a DATE column** — the time is words. It is stamped on the call note as `Call back Thu Oct 9 around noon ·` and written to `reach_notes` ONLY when the lead has none (`reachFromWhen`), so a standing "Tue/Thu mornings" is never overwritten by one appointment. The contact strip also captures **their email** (`leads.email`, rides the main PATCH); a gatekeeper save that captured a new email opens EmailModal on the **🚪 Front desk referred** preset (default for status `gatekeeper`) — "thanks for taking our call" is wrong for someone she never spoke to.
- **Callback info = `leads.reach_notes` (migration `010_leads_reach_notes.sql`)** — what the desk says about reaching the DM ("Tue/Thu mornings", "after 2pm", "ext 204"). Saved in the same separate PATCH as `dm_phone` (`saveDmExtra`), shown in yellow 🕐 in the strip, the gatekeeper panel (beside the date, "pick the date to match"), the dialer ASK FOR banner, lead rows and Follow-Ups rows. **Named contacts sort first** in Follow-Ups `byUrgency` (after status rank — `gatekeeper` is now rank 3, above routine retries) and in Day Plan rung 3 (due callbacks): the day-2 "Hey, is Mike around?" call is the script's payoff and must not sit behind unnamed retries.
- **Contract renewal = `leads.contract_ends` (migration `011_leads_contract_ends.sql`, stored as the 1st of the month)** — the step-2 answer. The 📆 block shows on every answered outcome except interested_no_dm (Vendor Status chips appear there too when qual isn't already required, so "we're in-house, not interested" still records vendor status). A newly captured month stamps `[renews YYYY-MM]` on the call note and books a callback `RENEWAL_LEAD_DAYS` (75) before the 1st via `renewalCallbackDate()` (never earlier than tomorrow) unless the call already set a date. **On Not Interested that renewal callback flips the LEAD status to `callback`** (the call row stays not_interested — that is what they said today) so the lead stays in Follow-Ups / Day Plan instead of dying. Shown as a purple 📆 Renews chip on dialer pills, lead rows, Follow-Ups rows and the contact strip.
- **Script funnel** `GET /api/analytics/script-funnel?days=42` (admin, `compute_script_funnel`) + `ScriptFunnelPanel` (Analytics) + a 📇 line in the daily digest. Reads only `call_outcomes`: dials → reached (`CONTACT_OUTCOMES`) → gatekeeper → names (`DM: ` stamp) → day-2 dials (later calls on a lead whose earlier call carried the stamp) → DM reached (contact, not gatekeeper) → engaged (`ENGAGED_OUTCOMES`) · renewals (`[renews` stamp). Names/day-2 are 0 before 2026-10-07 by construction — that is the baseline, not a bug. Never re-inline the outcome sets here.
- **`dm_phone` is NOT the switchboard.** `phone` stays UNIQUE (007) and is what dedupe, the dialer queue and the Twilio bridge key on; `dm_phone` is a plain text column, not unique. `POST /api/call/start` accepts an optional `phone` override but only a number already on that lead (`phone` or `dm_phone`, last-10-digit match) — it must never become "dial anything through our Twilio". It reads the lead with `select=*` so it keeps working before 009 runs.
- **Visibility**: dialer card shows a blue 👤 ASK FOR banner (name + title) under the company and a 📱 Direct line block ABOVE the main line (tel: + ☎️ Call direct); with a direct line on file the opening-line box leads with step 2. Lead rows carry a `👤 ASK FOR <FIRST>` chip first in the chip row and the direct line under the main number; a **👤 Named Contact** quick filter (`namedOnly`) sits beside Needs Another Call. `buildOpener` returns `direct` for this.

### 📝 Notes → lead fields (2026-10 — work around how she writes, don't retrain her)
- **Why:** Oct 7, 16 of 25 gatekeeper calls named the decision-maker, and 3 gave a direct number. All of it was typed into the call NOTE ("reps said - Pamela 573… is the decision maker", "Tammy is not in today, back tomorrow"), and **0** reached the lead, because the 👤 fields sit elsewhere in the form. Two notes also said "corporate handles the cleaning" without the 🏢 box ticked.
- **Extractor:** `extractNoteDetails(note, lead, today)` (App.jsx) and `extract_note_details()` (main.py) are **line-for-line mirrors**, pure, never throw. They return name / title / phone / email / when / cbDay / vendor / contractEnds / corporate / hqPhone.
  - Parity: cross-checked on 24.5k cases (every note in call_outcomes + 5k fuzzed with odd whitespace, Unicode digits and casing) with **0 mismatches**.
  - Python is ASCII-mode with `\Z` anchors and both sides normalise exotic spaces first. That is what makes them agree; **change both together and re-run the parity check.**
- **Rules that keep it safe:**
  - A decision-maker cue (owner / manager / "the name is" / "decision maker") outranks a mere mention ("spoke to").
  - "Brianna, the rep" / "Nick from HR" are not DMs.
  - "Carson Tahoe Hospital" and "the Petco corporate decision" are businesses, not people.
  - A number labelled main / office / hospital / corporate is not a direct line, and a number is only taken when the note also names someone.
  - Same-day phrases ("off today", "in 30 min", "after an hour") never become standing callback info.
  - Vendor phrases respect negation ("not looking for a vendor" ≠ Open to Options).
  - "Dr. Webb" is saved as firstName "Dr. Webb" (`splitPersonName` / `split_person_name`), so the script never says "is Dr. around?".
- **CallModal:** an effect fills ONLY fields that are empty on the lead AND that she hasn't typed into (`fxAuto` remembers our own values, so they can be updated or withdrawn as the note changes; hers are never touched).
  - Each value shows as a chip under the note ("📝 From your note — saving:"); ✕ reverts it and `fxSkip` stops it re-filling.
  - Everything rides the existing save path, including the `DM:` stamp the script funnel counts, the dm_phone/reach_notes PATCH, and the referral email.
  - The callback day only replaces a blank date or the gatekeeper default, never her pick.
  - Corporate ticks the box. **Cancel on the sibling list then saves the call without parking it** (she never asked for the park, so she must never lose the save).
- **Old-browser safety net:** the client sends `note_capture:"v1"` on `POST /api/calls`, and log_call pops it before the insert.
  - Without it (a stale bundle, or another API caller), `_note_safety_net()` runs the Python extractor after the save and writes via `write_note_fields()`: **conditional PATCHes** (`firstName/lastName`, `email`, `dm_phone`, `reach_notes` each filtered to still-empty, so a race can't overwrite).
  - It writes one PATCH per group, so a missing 009/010 column only loses its own field, then stamps `DM:` on the call. It never raises.
- **Backfill:** `POST /api/admin/backfill-note-details?days=14&dry_run=1` (dry_run is the DEFAULT).
  - The latest mention per lead wins, through the same empty-only rules and the same conditional writes.
  - `filter_selfcheck` first PATCHes a non-existent id (`id=eq.-1`) with every filter, proving syntax and columns before any real write.
  - Corporate mentions are **reported only**; parking a chain stays a person's decision.

### ✉️ "Send an email to …" — opened at save, sent at end of day if she forgets (2026-10)
- **At save:** any call where a NEW email reached the lead (typed in 👤 or read from the note) opens EmailModal after the CallModal closes, on **every outcome**. Most of her "send an email to …" notes are logged as no-answer, and it used to open on gatekeeper only.
  - Script choice: decision-maker talked → ✉️ *Asked for info*; the desk named someone → 🚪 *Front desk referred*; a generic inbox → *Asked for info*. It is passed as `lead._emailPreset` (UI-only, never saved).
- **End of day:** `run_eod_email_sweep_if_due()` (bg loop, once per UTC day on/after `EOD_EMAIL_HOUR_UTC`=23, ≈4pm PT after her 9–2 shift) sends what she didn't. It sends ONLY when ALL of these hold:
  - that call's note asks for an email (`note_asks_for_email`: "send an email/info to", "better to email", "gave the email", "through email"…, and NOT "I already sent");
  - the address is IN that note (never guessed, never the lead's Apollo address), well-formed, and not ours (`email_address_ok`);
  - the address is not on the suppression list, and the lead is not `do_not_contact`;
  - **nothing went to the lead since the call** and nothing went to that address in `EOD_EMAIL_DEDUPE_DAYS` (14). That covers email_log *and* campaign_sent audit rows. A failed dedupe read counts as "already sent" (fail-closed);
  - the call is older than `EOD_EMAIL_GRACE_MIN` (30), the latest asking call per lead wins, and at most `EOD_EMAIL_MAX` (20) go per day.
- **Day stamp is written BEFORE any send and a failed stamp = no sends** (same rule as the weekly jobs). A twin service whose settings writes are RLS-dropped must never be able to email prospects.
- Each send is logged exactly like a manual one (`email_log`, sent_by = the caller), plus a `send_email_auto_eod` audit row and one Slack summary of every address.
- **Templates:** `eod_email_content()` mirrors the EmailModal "asked"/"referred" presets. **Change the wording in both places.**
- **Admin:** `POST /api/admin/eod-emails` — `dry_run=1` (DEFAULT) lists would-send + skip reasons; `dry_run=0` sends now (dedupe still applies).
- **Kill switch:** `EOD_EMAIL_ENABLED=0`.

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
- `GET /api/leads/{lead_id}` (single-lead, powers frontend targeted refresh) MUST stay AFTER `/api/leads/lookup` and `/api/leads/emailed-flags`
- FastAPI matches routes in order — wildcard catches everything if first

### 2026-08 pipeline recalibration (don't regress these)
- **Retirement**: `RETIRE_AFTER_DIALS` (default 4) — log_call auto-parks status `retired` on the Nth no-contact dial (status still new/no_answer); excluded from dialer queue, NO_DIAL sets, stale-recycle, and guidance availability counts. `POST /api/admin/retire-exhausted?dry_run=1` sweeps the backlog (skips leads with ANY historical contact outcome). Manual status edit un-retires.
- **Targeting (data-driven, 11.5k-call audit)**: hospitals/nursing/public schools = in-house janitorial (5.8% connect, 0.4% engaged) → demoted to a 14-pt fit tier, dropped from NPI taxonomies, CMS fetchers (default `CMS_HEALTH_TYPES=dialysis`), and OSM selectors. Dialysis/urgent-care/clinics/daycare + manufacturing/logistics were the priority tiers — **superseded 2026-10, see "2026-10 best-vertical targeting"**: on Cristine's own calls manufacturing/logistics answer but almost never buy. DaVita/Fresenius deliberately NOT chain-penalized (their local managers book walkthroughs). Guidance auto-pull now passes `GUIDANCE_PULL_INDUSTRIES`. Permits no longer dial contractor_phone.
- **Phone validation**: `lookup_phone_line()` + `POST /api/admin/validate-phones` — env-gated on TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN (~$0.008/lookup); dead numbers → do_not_contact + `[phone:dead]` notes tag.
- **Dialing**: tel: links (caller's Google Voice) remain the fallback; Twilio click-to-call with market-matched caller ID is the primary path once TWILIO_NUMBERS is set (see Click-to-call section).

### 2026-09 network hardening (don't regress these)
- **`req_lib` is no longer the bare `requests` module** — it is `_RetryingHTTP`, a per-thread `requests.Session` (Session is not documented as thread-safe and this process runs a bg thread beside the request handlers) with connection pooling and a urllib3 `Retry`. Production logged `[INV-REFILL] Failed to resolve 'ucpwpjokyconwzwqvdad.supabase.co'` every ~10 min for 7h because the module-level API does a fresh DNS lookup per call with **zero** retries, so one resolver blip killed the low-inventory refill outright. Envs: `HTTP_RETRY_TOTAL` (0 disables), `HTTP_RETRY_CONNECT`, `HTTP_RETRY_BACKOFF`.
- **The retry policy is deliberately ASYMMETRIC — never make it uniform.** connect/DNS errors retry for *every* method (the request never reached the server, so a replay can't duplicate). read errors and 5xx retry only for urllib3's default idempotent `allowed_methods`, so **POST/PATCH are sent exactly once**: a retried `POST /call_outcomes` would double-log a call and corrupt `total_calls`. `raise_on_status=False` because ~250 call sites inspect `r.status_code`.
- **OSM**: 5 mirrors (was 2 — production had BOTH failing on one pull), tried in a rotating order (`_overpass_order`) so a slow instance isn't always first. Per-attempt timeout is clamped to the time left in the budget — it was a flat 40s against a 35s budget checked only *between* categories, so one slow mirror overran the whole budget. Budget default 35s→90s. `OVERPASS_ATTEMPT_TIMEOUT` (20s).
- **A source outage must never look like an empty result.** `source_osm` returns `sourceUnavailable` + `mirrorErrors`, and the summary says "SOURCE OUTAGE" when 0 elements came back *and* every mirror errored. Before this, a week of dead Overpass mirrors read as `found=0 saved=0`, HTTP 200 — i.e. "OSM just has no leads for us".
- **`_paginated_get` truncation is loud.** A non-2xx or exception mid-walk returns a PARTIAL list that is indistinguishable from "end of data" to every caller, so stats/analytics/lead lists silently under-report. It now logs `TRUNCATED at rows X-Y` with the status/body, plus a warning when the `max_pages` ceiling is hit.

### 2026-09 source + logging fixes (don't regress these)
- **Walkthrough capture shows on `callback` too** (was interested/converted only). "She set the walkthrough for Wednesday" reads as a callback to the caller — there IS a date to ring — so that was the button pressed and the 📅 field never appeared. Notes audit: **9 of 11 genuinely-booked walkthroughs created no `appt_*` record** (5 as callback, 4 as no_answer), so the Appointments board, Angelo handoff, `_get_walkthrough_followups` and `_notify_walkthrough_client_call` were all silent on live deals. Never narrow this gate again.
- **Caller metrics are not comparable across the Jun-8 caller switch.** The prior caller's 20.7% "contact rate" has corroborating evidence (≥20s talk, notes, or qual) on only **20%** of its claimed contacts vs **92%** for Cristine; median modal time on a claimed contact was **2s** vs 32s, and one day logged 333 dials. Their 12 `converted` rows are real but meant "booked an appointment" — Cristine records the same event as callback/interested + a note, which is why conversions read 12 vs 0. Use evidence-backed counts (talk time / notes / qual / appt records), never raw outcome tallies, for any before-vs-after comparison.
- **`gatekeeper` outcome** — 4th step-1 primary in CallModal (shortcut **4**; 1/2/3 unchanged on purpose). "A person answered, no DM." Callers were logging these as No Answer, which understated true reach by ~6.5pt over 14k dials AND left no callbackDate so the lead fell out of the pipeline. Requires a follow-up date (prefilled +3d). In `CONTACT_OUTCOMES`, deliberately NOT in `ENGAGED_OUTCOMES`. In `ENGAGED_STATUSES` (dedup protection, not a metric). Survives ⚡ quickLog demotion. Retires at **2× `RETIRE_AFTER_DIALS`** — the number is proven live, but must not be immortal.
- **`CONTACT_OUTCOMES` is now the single source of truth.** Six copies of that literal had drifted; two (connectivity heatmap, best-hour ranker) were missing `interested_no_dm`, so the same call counted as contact on the leaderboard and no-contact on the heatmap. Never re-inline it.
- **NPI statewide pull was querying ONE taxonomy.** The loop requested 200 rows (= API max = `FREE_SOURCE_MAX_ROWS`) for `NPI_STATEWIDE_TAXONOMIES[0]` then broke on the budget, so the other six were unreachable — and with no `skip` it re-fetched identical rows every run, which all deduped away. Now: budget split evenly across taxonomies + a persisted per-state `npi_skip_<ST>` offset so each pull goes deeper. Also drops NPPES `basic.status != "A"` (deactivated = closed), fail-open on a missing field. **Self-heal:** a zero-row pass at a non-zero offset rewinds to page 1 and resets the offset — without it, a rejected/unsupported `skip` (or a small state whose offset ran past the end) would make every later pull return 0 rows *silently*, since `_npi_query` swallows errors and returns `[]`.
- **`dead_rate` is caller-dependent, not a source property.** It mines free-text notes, and note coverage on no-contact rows was 5% before the Jun-8 caller switch vs 73% after. Never compare `dead_rate` across that boundary, and never read a low `dead_rate` as "good numbers" when the caller isn't writing notes.
- **Permits `$order` never worked.** `_pick_field` returns a LIST, so `f"{f['date']} DESC"` sent `['issue_date'] DESC`, Socrata 400'd, and every pull fell through to the unordered retry — arbitrary order, not newest-first. Use `f["date"][0]`. `days` is now a real recency floor (`$where`) and its own `FreeSourceRequest.days` field; it used to be accepted and ignored, and `body.limit` was doubling as the window.
- **OSM**: `'["office"]'` catch-all replaced with 17 curated private-tenant subtypes — `office=*` includes government/diplomatic/political_party/religion/charity (in-house custodial + switchboards; the caller's notes carry "wrong number government building"). Also skips OSM lifecycle-prefixed dead features (`disused:`/`abandoned:`/`was:`/`removed:`/`demolished:`/`razed:`, `office=vacant`, `operational_status=closed`) — guaranteed dead numbers that used to ingest as normal leads.
- **Review scan** now filters to leads that can actually reach Day Plan rung 4 (`COMPLAINT_RUNG_STATUSES`, `COMPLAINT_RUNG_MAX_CALLS`, phone required) BEFORE spending ~$0.05/lead, and gates complaint age at `COMPLAINT_MAX_AGE_DAYS` (540) — Google returns ≤5 reviews so "most recent complaint" was often years old. Keep these in step with `dayPlanRungs()`.

### 2026-08 audit fixes (don't regress these)
- **All App hooks above `if(!user) return <Login/>`** — a hook below it crashed React to a blank screen on every login/logout (proven live).
- CallModal PATCH must NOT send `callbackDate:""` on neutral outcomes (wiped scheduled callbacks); clears only on not_interested/converted.
- ⚡ quickLog: optimistic single-lead update (no full loadLeads), never demotes engaged statuses, 20s undo via `POST /api/calls/{id}/undo` (own call, ≤2min).
- `tsLocalDate(ts)` for ANY timestamp-vs-business-date compare (UTC slice [:10] breaks after ~8pm ET). Backend: `local_day_start_utc()` for calls-today.
- Dialer/Follow-Ups/Dashboard widgets derive from `allLeads.length?allLeads:leads`, never the filter-scoped `leads`.
- log_call stores `calledBy = authenticated caller` (spoof defeated anti-gaming). Sequencer has a 48h `_recently_sent` audit-row dedupe (fail-closed) so a failed post-send PATCH can't cause an email re-send loop.
- Nudges (email-queue, walkthrough) cooldown on the UTC date to match the UTC hour gate — ET-date cooldown fired them at midnight ET.
- Leaderboard responses cached 30s (`_leaderboard_cache`); GZipMiddleware on; `/api/leads?…` refetch after saves replaced by `refreshLead()`; segBase/displayLeads are useMemo'd; lead sections DOM-capped at 100 rows.
- `supabase_indexes.sql` at repo root — run in Supabase SQL editor after major query changes.

### 2026-10 best-vertical targeting + refill volume (don't regress these)
- **Targeting comes from Cristine's OWN outcomes**, never the pre-Jun-8 audit. Jul 1–Sep 30, 6,416 dials, interested/callback/converted per 100 dials (avg 1.2): dental 3.8 · dialysis 2.5 · urgent care 2.2 · medical equipment 1.6 · nursing/assisted 1.4 · hospitality 1.4 · generic clinic 1.1 · warehouse/logistics 0.8 · office 0.6 · mental-health/rehab 0.25 · manufacturing 0.0 (223 dials). Industrial answers at 9–12% (best pickup we have) and doesn't buy; a day of high pickups with zero interest was exactly that mix.
- `CLEANING_FIT_TIERS` encodes this: 40 for dialysis/dental/urgent care/medical equipment; mental-health/rehab (14) listed BEFORE the clinic tier so "Clinic/Center, Mental Health" can't score as a clinic; manufacturing/logistics and office at 16. Score is the tie-break that orders fresh leads in the dialer, so it must track **interest, not pickup**. Scores are stamped at insert — run `POST /api/admin/rescore-all` after any tier change. Keywords are substrings: use "renal disease"/"esrd", never bare "renal" (matches "Adrenaline").
- **Refill volume was the real starvation.** `run_scrape` divides its limit across every industry × city, so the old hard-coded `limit=30` gave 3 results per search and 20–46 new leads per refill against ~140 first dials/day. Now `REFILL_PLACES_PER_LOCATION` (60), `REFILL_MIN_FRESH` 300 (~2 days), `REFILL_COOLDOWN_HOURS` 12 (was a hard-coded 20).
- **Every refill runs free sources first** (`_refill_free_sources`): CMS 1–2★ dialysis + NPI statewide (dialysis/urgent care/surgical/dental, offset goes deeper each run) per state, OSM `Dental` per metro. Places only runs if those saved < `REFILL_SKIP_PLACES_IF_FREE_SAVED` (150). Each source fails soft; a Places spend-cap refusal never hides free leads that landed. Still gated on `GOOGLE_KEY`, which also keeps a key-less twin service from running it.
- `WEEKLY_REFILL_INDUSTRIES` default is `Dialysis Center,Dental Office,Urgent Care,Medical Equipment`. **Railway sets this env explicitly** — a code default change does nothing until the variable changes too.
- The job-posting feed is paused by default (`JOBS_REFRESH_ENABLED=0`): mostly warehouses, 0 interested in its first 36 dials. Manual `POST /api/sources/jobs` still works.
- The restaurant-inspection feed only has datasets for Chicago, NYC and Austin, so `HEALTH_REFRESH_STATES` = NV,OH,MO can never return restaurants; the weekly refresh now logs that instead of a silent `new=0`. Adding NV/OH/MO needs those cities' own open-data inspection feeds.

### 2026-10 running call tally (don't regress these)
- **The caller's count lives on `GET /api/quota` → `tally`** (`call_tally()` in main.py): dials / answered / no_answer / voicemail / other, plus gatekeeper / not_interested / interested as breakdowns OF answered. no_answer + voicemail + answered + other always sum to dials. "answered" is `CONTACT_OUTCOMES` and "interested" is `ENGAGED_OUTCOMES`, so the tally can never disagree with the leaderboard — never re-inline those sets in the tally or in App.jsx.
- `my_calls_today` is unchanged (same query, now `select=outcome`), so older clients keep working; `tallyOf()` in App.jsx renders a dials-only tally if `tally` is absent.
- Shown in three places from one component, `TallyStrip`: the header pill (hidden under 1200px), the dashboard quota card, and "Today so far" in the Dialer.
- The ⚡ quick-log and its undo apply `tallyBump()` (±1, no_answer/voicemail only) for instant feedback, then refetch; the server value always wins. `quickLog` previously never refreshed the quota bar at all.
- A 60s poll + refresh-on-focus covers CallModals mounted inside sub-panels (Walkthroughs, Qualified, My Week) that don't call `refreshLead`.

### 2026-10 corporate-controlled chains (don't regress these)
Oct 1–5: one urgent-care chain (American Current Care) was 72 leads and 9% of the week's dials, and every receptionist said corporate handles cleaning. NPI files each location as its own organisation, so the refill kept handing her the next one.
- **🏢 Corporate handles cleaning** (CallModal toggle on gatekeeper / not_interested) → `POST /api/leads/{id}/corporate`. **`dry_run` defaults to true** and the modal shows the sibling list in a confirm dialog BEFORE anything saves. The real run parks this lead plus every open sibling (`_CORP_PARKABLE_STATUSES` = new/no_answer/called/gatekeeper) as `retired` with a `[corporate-decides]` note; the PATCH carries `status=not.in.(interested,…)` so an engaged location is never touched. Records the chain in `app_settings.corporate_chain_stems` and, if the caller got the HQ number, ingests it as one `[warm-list]` lead under `CORPORATE_HQ_SOURCE`. Admin: `GET /api/admin/corporate-chains`, `POST /api/admin/corporate-chains/remove`. Reversible — a manual status edit un-retires.
- **`chain_stem()`** = the first TWO significant words of the company name. A third word is usually the city ("Aspen Dental - Columbus"), which split one chain into many. Returns empty for a generic name ("Family Urgent Care", "Las Vegas Day School", "Law Offices of …") so such a name never matches siblings or gets blocked. Checked against all 11.4k live leads; extend `_CHAIN_GENERIC_WORDS` if a place word starts clustering unrelated businesses.
- **`ingest_leads`** drops leads from a corporate-blocked chain (`droppedCorporate`) and caps one chain at `CHAIN_MAX_PER_INGEST` (3) per ingest (`droppedChainCap`) — enough to learn whether it's corporate-run, not enough to fill a day. **Dialysis is exempt from the cap** (`_CHAIN_CAP_EXEMPT_RE`): DaVita/Fresenius local managers book walkthroughs, and the cap must never thin the top vertical. `corporate_chain_stems()` is cached 5 min and fails open to the last load so a settings blip can't stop an ingest.

### 2026-09 weekly-job guard + demoted retirement (don't regress these)
- **Every weekly job stamps its cooldown row BEFORE it works, and `_iso_week_due` reads that same row** — so a stamp that silently fails re-fires the job on EVERY 10-minute tick. `_record_weekly_run` now returns False (and logs `[WEEKLY-COOLDOWN]`) on a non-2xx/exception, and every caller treats False as "not this tick". Never drop that `if not _record_weekly_run(...): return` — for the Places refill each extra run is real money.
- **ONE Railway service must run this backend.** On 2026-09-30 a second service in the LeadFlow project (`worthy-nature`, Railpack build, port 8080, env = only the `VITE_*` vars, auto-deploying `main` since at least 2026-06) was found running the same `main.py` against the same Supabase with the **anon** key as `SUPABASE_SERVICE_KEY`. It answers 502 over HTTP, but its `_bg_maintenance_loop` was live: its `app_settings` upserts are RLS-dropped, so every weekly stamp failed silently and it re-ran the health refresh and the demoted sweep on **every 10-minute tick**. Every scheduled job gets duplicated by a twin like this; if a cooldown "doesn't hold", check `list-services` before the code.
- **`retire_demoted_verticals` holds `_RETIRE_DEMOTED_LOCK` (non-blocking; second caller gets `{"busy": true}`).** The first scheduled sweep on 2026-09-30 ran on both services at once and reported 1,801 + 1,433 for 1,828 rows actually parked — each counted the other's PATCHes. The on-disk result was correct (204 had-contact leads protected); the *report* was not. The lock is per-process, so it only stops in-process overlap; the fail-closed stamp above is what stops a mis-configured twin from looping.
- Its `leads` walk is `order=id` and deduped by id: `_paginated_get` with no ORDER BY pages heap order, and an UPDATE landing mid-walk shifts every later page. The PATCH carries `&status=not.in.(engaged|retired)` and `retired` is counted from the `return=representation` body, never assumed from the batch size; a non-2xx batch is logged, not counted.

### 2026-10 due follow-ups lead the dialer (don't regress these)
- Oct 1–5: 36 gatekeepers handed over a decision-maker's name and a time; **none was called back, and 49 callbacks sat due.** The queue's first key after the prospect's clock was "fewest calls", so every lead she had already reached sorted behind ~800 never-dialled ones.
- `is_due_followup()` (main.py) / `isDueFollowUp()` (App.jsx) — **keep them in step.** Due = `callbackDate` ≤ today, at most `FOLLOWUP_MAX_OVERDUE_DAYS` (21) late (older strays stay on the Follow-Ups tab instead of taking over the dialer), status not done, not tried in the last `FOLLOWUP_RECALL_HOURS` (3) so one no-answer doesn't pin it to the top. It is the FIRST sort key ahead of the tz bucket, **except a due lead whose office is shut (TZ_OFF) doesn't jump** — the tz rule is still a sort key, never a filter.
- `/api/dialer/queue` fetches due rows **separately** (`callbackDate` between oldest-due and today) and merges them before the snooze/NANP filters, because due leads have the most calls and fall past the 5000-row ceiling of the main fetch. Response carries `due_followups`. The card shows an ⏰ banner above the 👤 ASK FOR block, and the header shows "N follow-ups due first".

### 2026-09 prospect-local dialer ordering (don't regress these)
- **The dialer sorts by the clock at the desk being RUNG, not the caller's.** Cristine's 9am–2pm PT shift is 9am–2pm in NV, **11am–4pm in MO and 12pm–5pm in OH** — she never reached an Ohio prospect in their morning, and the queue (ordering by `total_calls`, then `last_called_at`, then `score`) was blind to it. `dialer_tz_bucket()` buckets a lead PRIME / LUNCH / OFF by its own local hour; that bucket is the FIRST sort key, the old ladder is preserved inside each bucket.
- **It is a sort key, never a filter.** Off-hours leads sink to the back and stay in the queue — if the only stock left is Ohio at 5pm ET she still needs numbers to dial. Never turn this into a `filter`.
- **FAILS OPEN on an unplaceable lead**: blank/unknown state buckets off `LEADFLOW_TZ_OFFSET_HOURS` (same contract as the connectivity heatmap), never straight to OFF. Burying every state-less lead would quietly starve the queue of dialable stock.
- **The window is a COVERAGE heuristic, not a measured optimum.** Within-month analysis found **no caller-local time-of-day effect** (April 6–8am 19.3% vs 9am–1pm 19.0%; May 20.3% vs 21.8%) — the earlier "mornings are better" read was a confounded artifact (early hours only existed in good-stock months). The prospect-local effect is still **unmeasured**; that is why the hours are env-tunable rather than hard-coded. Rank them from `GET /api/analytics/connectivity` (`state_breakdown` + day×hour) once enough dials have accumulated, then retune the envs — no deploy.
- **`/api/dialer/queue` collects every eligible row BEFORE trimming.** It used to `break` at `limit`, so only the first 50 of up to 5000 fetched rows were ever considered — any reordering would have been reshuffling an already-truncated slice. Fetch cost is unchanged; the `limit` now applies after the sort, so a short queue is the *best* leads.
- **The queue must not stamp fields onto lead rows.** Bucketing is decorate-sort-undecorate. These dicts are whole `leads` rows and callers PATCH them straight back; Supabase rejects an ENTIRE write naming an unknown column, so a cosmetic `_tz_bucket` key could break a lead save far from here.
- **ONE timezone table.** `US_STATE_TIMEZONES` in main.py is canonical and ships to the client via `/api/call/config` → `dialer_tz.state_timezones` (`_tz_lookup_for_client()`, keyed by abbreviation AND upper-cased full name so the JS needs no name-matching of its own). `STATE_TZ` in App.jsx is now an **offline fallback only** — `resolveTz()` prefers the server copy. Do not extend `STATE_TZ`; extend the backend map. It was already missing PR/VI/GU/MP.
- `tzBucketsFor(cfg, now)` in App.jsx is module-level and pure specifically so it can be cross-checked against `dialer_tz_bucket()` — 10,176 (instant × state) decisions agree, including both US DST transition days. Frontend and backend disagreeing would order her queue one way and the server's another for the same lead.
- **The 5-min retick is held while a call modal is open.** A retick re-buckets → re-sorts → and `dialerIdx` is a POSITION, so the card behind the modal would become a different lead. Nothing is mislogged (CallModal closes over its lead object), but hanging up to a different company on screen is its own bug.
- The dialer's `offHours` badge (8am–7pm local) is a **safety** warning — deliberately WIDER than the prioritisation window. Different jobs, different bounds; don't merge them.
- Envs: `DIALER_TZ_ORDER` (0 = instant revert to the flat ordering), `DIALER_LOCAL_START_HOUR` (8), `DIALER_LOCAL_END_HOUR` (17, exclusive), `DIALER_LOCAL_LUNCH_HOURS` (12).
- **Best-hour ranking (2026-10) — the tz sort key is now a RANK per local hour, not the PRIME/LUNCH bucket.**
  - **Data:** Jun 29–Oct 7, 7,160 dials, measured as the rate at which a non-gatekeeper picked up, by the prospect's local hour, **with each state's own rate subtracted** (so "10am" isn't just "Nevada"). Results: 10am 11.4% · 11am 8.7 · 2pm 8.4 · 1pm 8.1 · 9am 7.3 · 3pm 7.3 · noon 7.1 · 4pm 4.2. So noon IS the dip and 1pm is not; don't add 13 to `DIALER_LOCAL_LUNCH_HOURS` on a hunch.
  - **Ranking:** `dialer_hour_ranks()` turns the stored scores into a dense rank (0 = best) for open hours; off-hours rank `TZ_RANK_OFF` (99). The queue (`dialer_tz_rank`) and the client (`tzRanksFor`, from `dialer_tz.hour_rank` in `/api/call/config`) sort on it, and every 5-min tick re-sorts. **Still a sort key, never a filter; due follow-ups still lead; OFF still last; buckets still drive the OFF test, the labels and `tz_buckets` counts.**
  - **THE RANK IS A TIE-BREAK UNDER `total_calls`, NEVER ABOVE IT (2026-10-09 regression).** Order outside the due group is: OFF last → fewest calls → best-hour rank → oldest contact → score, identical in `_key()` (main.py) and the dialer sort (App.jsx). For two days the rank was the first key: the one timezone at its best hour put its entire once-called April backlog (4,000+ WY/MT/UT rows from the previous caller) ahead of 369 never-dialed GA/OH/NV/MO leads, and the 4h snooze re-served the same April rows that afternoon — Cristine dialled the same stale leads on Oct 7, 8 and 9 while fresh stock sat untouched and refill was working fine (+309 Oct 2, +304 Oct 5). Best hour chooses AMONG equally-fresh leads. Verify with `GET /api/dialer/queue?limit=200`: the top of the queue must be `total_calls` 0 whenever the fresh pool is non-empty.
  - **Scores:** `run_dialer_hour_scores_if_due()` (bg loop, every `DIALER_HOUR_SCORE_REFRESH_HOURS`=24) walks `DIALER_HOUR_SCORE_DAYS` (90) of calls and stores `app_settings.dialer_hour_scores`.
    - They are shrunk toward a prior with `DIALER_HOUR_SCORE_PRIOR` (200) pseudo-dials; the prior is the overall rate, with configured lunch hours penalised `DIALER_LUNCH_PRIOR_PENALTY` (0.15).
    - Below `DIALER_HOUR_SCORE_MIN_DIALS` (500) the scores ARE the priors, so the ranks reduce to exactly the old PRIME-then-LUNCH order. A failed read does the same.
    - **Request paths only read** (30-min cache), never compute.
    - The recompute is guarded by an **in-process** attempt clock, not the stored row, so a twin whose settings writes are RLS-dropped can't re-walk the call table every tick.
  - **Parity:** frontend and backend ranks were cross-checked (25,440 instant×state decisions, both DST days, 0 mismatches). An old server without `hour_rank` → client rank = bucket.
  - **Kill switch:** `DIALER_HOUR_RANKING=0` → prior ranks, i.e. the previous behaviour. Inspect via `GET /api/admin/dialer-hour-scores` (`?recompute=1` rescores now).

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
- `INSIGHTS_MODEL` — pattern-insights / weekly-digest model (default: claude-opus-4-8). Low-frequency, multi-note synthesis → top tier; spend negligible at weekly cadence. Falls back to HAIKU_MODEL if it errors. (max_tokens 4000 — rich JSON was truncating at 1600.)
- `WEEKLY_DIGEST_ENABLED` — auto weekly AI review to Slack (default: 1)
- `WEEKLY_DIGEST_DAY` — weekday to send (0=Mon … 6=Sun, default: 0)
- `ANGELO_SLACK_WEBHOOK_URL` — Slack webhook for the appointment→Angelo hiring handoff (falls back to SLACK_WEBHOOK_URL if unset)
- `DAILY_DIGEST_ENABLED` / `DAILY_DIGEST_HOUR_UTC` — daily digest self-schedules from the bg loop, once per UTC day on/after the hour (default 1 ≈ 9pm ET). No Railway cron needed.
- `CRON_SECRET` — optional; lets an external cron hit /api/daily-summary or /api/weekly-summary via ?secret= (they otherwise require an admin token)

## Follow-Ups tab structure (2026-08)
Buckets in priority order: 🚶 Walkthrough Follow-Ups → 🚨 **Overdue — Untouched** (no call since callbackDate came due; `last_called_at < callbackDate`) → ✆ **Overdue — Attempted** (called since due, still on old date; shows tried-date chip) → Today → This Week → Next 30 Days → Later → Future. Near-term buckets sort Interested > interested_no_dm > callback > rest, then oldest date. Headers toggle collapse (`fuCollapsed` state at App level — never inside the IIFE); month/later/future default collapsed. Dashboard overdue banner shows the untouched/attempted split. This split exists so Eric can tell "caller ignoring it" from "logged via ⚡ quick no-answer, which deliberately doesn't touch callbackDate."

## Twilio inbound concierge (2026-08, INBOUND ONLY — never automates outbound)
`POST /twilio/voice` (+ /voice-fallback, /voice-done, /transcription) — point the Twilio number's Voice webhook here. During `INBOUND_FORWARD_HOURS_PT` (default 6-14) calls `<Dial>` through to `INBOUND_FORWARD_NUMBER`; after hours or no-answer, the concierge greets, `<Record transcribe>`s, files the voicemail as an inquiry (phone-matched lead gets a "📞 Inbound call (date): transcript" note prepended + callbackDate=today; unknown callers become source="Inbound call" leads), and pings Slack with the transcript + recording. `lastReplyDate()` in App.jsx matches both 📧 Reply and 📞 Inbound stamps → rung 1 of the Day Plan. Signature-validated via TWILIO_AUTH_TOKEN (X-Twilio-Signature vs APP_URL+path; `TWILIO_VALIDATE_SIGNATURE=0` to debug). Envs: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN (also activate /api/admin/validate-phones), INBOUND_FORWARD_NUMBER, INBOUND_FORWARD_HOURS_PT, INBOUND_GREETING, PT_OFFSET_HOURS (-7).

## Owned call transcription + call intelligence (2026-09) — Deepgram → Claude
**Twilio Conversational Intelligence is REMOVED** (`/twilio/intelligence`, `TWILIO_INTELLIGENCE_SID`, `_INTEL_SPEAKER` all gone). LeadFlow owns the pipeline: `recording-status` → `jobs` queue → Deepgram → `call_transcripts` → Claude → `call_analyses`.

**RUN `backend/migrations/008_call_transcription.sql` BEFORE setting `TRANSCRIBE_ENABLED=1`.** Both kill switches default OFF for exactly this reason.

- **Schema deviates from the spec deliberately.** The spec says `alter table calls` / `call_id uuid references calls(id)`. There is no `calls` table — calls live in `call_outcomes` and its `id` is an **integer**, not a uuid. FKs are `bigint → call_outcomes(id)`. The spec's DDL verbatim fails on statement one.
- **Transcription columns are written by PATCH, never on the call insert.** `call_outcomes` rejects an entire insert naming an unknown column, so putting `recording_sid`/`transcription_status` on the insert would break EVERY call save before 008 runs — i.e. stop the caller working. `_patch_call()` logs and degrades instead.
- **Queue** = `jobs` table polled by `_bg_maintenance_loop` (`run_jobs_once`, `JOBS_PER_CYCLE`=5 so a backlog can't starve digests/refills/sequencer). Idempotency is the partial unique index on `(kind, dedupe_key) where status in (queued,running)` — Twilio redelivers recording-status, and **a 409 from that index is the success path**, not an error. Retries 3× at 1m/5m/30m.
- **The gate is the whole cost design** (`transcription_gate`, pure function): `short` (< `TRANSCRIBE_MIN_SEC`=45) → `machine` (AMD) → `disposition` (rep logged voicemail/no_answer/busy) → duplicate. **Unknown/absent AMD FAILS OPEN** — the duration floor is the real guard; dropping real conversations because AMD was inconclusive is the costlier mistake. Every skip writes a `transcribe_skip` audit row; the histogram in `GET /api/admin/transcription-stats` is the "is the gate still working" view.
- **AMD goes on the `<Number>`, not the REST call.** The spec's table puts `machineDetection` on the outbound call — but `/api/call/start` rings CALLER_PHONE (our rep) first and bridges from TwiML, so that would run AMD against our own rep's handset. `TWILIO_AMD_ENABLED` (default 0) adds it to the prospect's leg.
- **Speakers**: dual-channel recording → Deepgram `multichannel=true`, exact attribution, no diarisation guesswork. `DEEPGRAM_AGENT_CHANNEL` (default 0 = Twilio channel 1 = the parent/rep leg) is the spec's verify-once — flip via env after the first real recording, don't edit code. Mono falls back to `diarize=true` with first-speaker=AGENT.
- **Analysis uses the `anthropic` SDK with `output_config.format`** (JSON schema enforced) — not regex-scraped prose like the weekly coach. `ANALYSIS_MODEL` default `claude-haiku-4-5`. **No `temperature`**: the current SDK doesn't accept sampling params; the schema provides the determinism.
- **A disposition disagreement is SURFACED, never applied**: `disposition_mismatch=true` when confidence ≥ 0.8 and `_dispositions_agree()` is false. That function maps the rep's outcome vocabulary onto the analysis enum so "mismatch" means real disagreement, not a vocabulary difference. Never overwrite the rep's entry — that's an admin review.
- `dnc_request` → lead `do_not_contact` immediately. A stated `callback_at` books a callback only when the lead has none. `hot_lead`/`appointment_set` → Slack.
- **Coaching block** (`call_analyses.coaching`): `approach`, `assertiveness{score,note,evidence[]}`, `close_opportunity{existed,taken,prospect_signal,missed_moment,say_instead}`. **`assertiveness` reads LANGUAGE, never tone** — a transcript cannot show vocal confidence, pace or volume, so the prompt forbids guessing at them and scores only hedging/minimisers/permission-seeking with verbatim evidence. The UI says so too. `passed_on_buying_signal` is **derived server-side** from `existed && !taken` rather than trusted to the model — that IS the definition. `close_rate_pct` denominator is *openings*, not calls; `coverage_pct` denominator is *human contacts*, not dials.
- **Admin UI**: `CallIntelligencePanel` (Analytics tab) = per-caller QA cards → flag-chip search over transcripts → inline transcript + analysis. Endpoints: `GET /api/calls/{call_id}/intelligence` (own call for a rep, **qa/coaching stripped for non-admins** — v1 does not show reps their scores), `GET /api/admin/transcripts/search` (PostgREST `plfts` over the 008 GIN index), `GET /api/admin/caller-qa`. `/api/calls/{call_id}/intelligence` has two path segments so it cannot shadow `/api/calls/qualified` or `/api/calls/history`.
- **Backfill** (rollout step 5): `POST /api/admin/transcribe-backfill?days=30&max_spend_usd=25&dry_run=1`. **dry_run=1 is the default** — it enqueues nothing and returns projected cost plus the skip histogram. The cap is enforced while BUILDING the queue, never while draining it: once a job is enqueued the worker runs it, so before-insert is the only place a ceiling can hold. Reuses `transcription_gate()` so a backfill can never transcribe something the live path would skip, and keys each job by `recording_sid` so a re-run after completion is 100% duplicate and free. Reads `audit_log` `call_recording` rows (the only place RecordingUrl is kept) rather than `call_outcomes.recording_sid`, which only exists post-008.
- Envs: `DEEPGRAM_API_KEY`, `DEEPGRAM_MODEL` (nova-3), `DEEPGRAM_RATE_PER_MIN` (0.0077, cost tracking only), `ANALYSIS_MODEL`, `TRANSCRIBE_MIN_SEC`, `TRANSCRIBE_ENABLED`, `ANALYZE_ENABLED`, `DEEPGRAM_AGENT_CHANNEL`, `TWILIO_AMD_ENABLED`, `JOBS_PER_CYCLE`, `TRANSCRIBE_DAILY_COST_ALERT` (5), `TRANSCRIBE_DAILY_COUNT_ALERT` (60).

## Click-to-call + recording + Claude call coach (2026-08)
- `POST /api/call/start {leadId}` (any caller): Twilio rings CALLER_PHONE (falls back to INBOUND_FORWARD_NUMBER), bridges to the lead via `/twilio/bridge` TwiML with a market-matched caller ID from `TWILIO_NUMBERS` ("+1702…:NV,+1614…:OH,+1816…:MO", first = default). `GET /api/call/config` gates the dialer's "☎️ Call — local caller ID" button.
- **Recording is ON IN ALL STATES** (`_should_record`, `RECORD_ALL_STATES_WITH_NOTICE` now defaults to **1**) — explicit owner decision, reaffirmed: the prospect hears an unambiguous notice before any conversation and can decline by hanging up, which is the standard implied-consent basis for recording into all-party-consent states. Includes NV (a primary market), CA, FL, WA.
- **THE INVARIANT — never record without delivering the notice.** `_should_record` returns False for *every* state when `RECORDING_ANNOUNCEMENT` is empty or whitespace. Consent-by-notice with no notice is just recording without consent, and a blanked-out env var must not be able to produce that silently. **Never reorder that check below the all-states branch.**
- The notice says "**is being** recorded", not "may be": it only plays when we ARE recording, and hedging weakens the very thing it exists to establish.
- `RECORD_EXCLUDE_STATES` (`CA,CT,DE,FL,IL,MD,MA,MI,MT,NV,NH,PA,WA` — all-party-consent for *telephone*; OR and VT are one-party for phone so deliberately absent) is retained as the **carve-out lever**: set `RECORD_ALL_STATES_WITH_NOTICE=0` and it governs again, with a blank/unknown state then failing closed.
- `record-from-answer-dual` → `/twilio/recording-status` audit row.
- **Spoken notice (2026-09)**: when recording, `<Number url="/twilio/announce">` plays `RECORDING_ANNOUNCEMENT` to the **PROSPECT's leg** after they answer and before the legs bridge. It MUST ride on `<Number url>` — a `<Say>` before `<Dial>` is heard only by our own caller, who already knows. Kept to one short sentence: it lands in the first seconds of a cold call. Env text is `_xml_escape`d (a bare `&` would invalidate the TwiML and Twilio drops the verb).
- Known limitation, by design: `record-from-answer-dual` starts at answer, so the notice is itself **inside** the recording — useful, it evidences that notice was given — but the prospect's "hello" precedes it. Nothing substantive is captured pre-notice.
- **Carrier-true talk time (2026-09)**: `<Dial action="/twilio/dial-status">` fires for **every** bridged call — recorded or not, in every state, because duration is metadata, not content. `DialCallDuration` is the LEAD leg (actual conversation), not the parent call (which would include ring time). `log_call` prefers it over the client value via `take_twilio_duration()` and tags the row `twilio_verified`. Handles both orderings: usually parked in `app_settings` (`twilio_dur_<leadId>`, TTL `TWILIO_DUR_TTL_MIN`=20) for `log_call` to consume once; if she already saved, the webhook patches the row in place **and clears a now-wrong `unsubstantiated_contact`**. This is what makes the substantiation flag meaningful — `call_outcomes.duration` is otherwise MODAL-OPEN time, which reads ~2s for anyone who dials elsewhere and logs afterwards.
- **Claude coach**: `POST /api/coach/run?days=7&preview=1` (admin) + weekly `run_call_coach_if_due()` (bg loop, cooldown `last_call_coach`) → INSIGHTS_MODEL scores calls (opening/discovery/objections/close), finds patterns, proposes script changes → Slack. No-ops until transcripts exist.

## Self-driving sourcing + warm list (2026-08)
- **Low-inventory refill** `run_inventory_refill_if_due()` (bg loop): when never-dialed dialable leads drop below `REFILL_MIN_FRESH` (250), fires `_do_refill_scrape()` immediately (20h cooldown row `last_inventory_refill`) — cristine can't run dry mid-week.
- **Weekly refill** `run_weekly_refill_if_due()` (bg loop, Monday ISO-week cooldown `last_weekly_refill`, record-before-work): Places scrape for 2 rotating metros (`refill_rotation_idx` in app_settings; `WEEKLY_REFILL_METROS` pipe-separated) × `WEEKLY_REFILL_INDUSTRIES`; Slack summary. Kill: `WEEKLY_REFILL_ENABLED=0`. ~$10/wk Places spend.
- **Weekly complaint scan** `run_weekly_review_scan_if_due()` (cooldown `last_weekly_review_scan`): 50-lead review batch over `WEEKLY_REVIEW_SCAN_INDUSTRIES`; Slack ping when flags found. Kill: `WEEKLY_REVIEW_SCAN_ENABLED=0`.
- **Slack warm-lead**: `POST /slack/warm-lead` slash-command endpoint (form token vs `SLACK_COMMAND_TOKEN`). `/warmlead Company, phone, note` → phone-deduped create/update with `[warm-list]` notes tag + callbackDate today. Warm list rung = `[warm-list]` tag OR source in angelo list / eric follow-ups / eric warm (`isWarmList()`).

## Day Plan (2026-08) — the calling priority ladder
### Complaint flow — hospitals are permanently excluded (2026-09)
Hospitals never surface as complaint leads: in-house EVS + multi-year system RFPs put them at 0.4% engagement over 519 dials. Enforced in four places so no config can reintroduce them — `add_intent_marker()` refuses complaint tags, `ingest_leads()` calls `strip_complaint_intents()` (CMS/health-inspection sources build notes as literal f-strings, bypassing add_intent_marker), `enrich_reviews()` skips them before spending Google calls, and `lead_intent_kinds()` / `parseIntents(notes, lead)` drop the tag at READ time so the ~45 legacy CMS hospital rows fall out of Day Plan rung 4 and lose the intent score boost with no data migration. `fetch_cms_hospitals` is unregistered from `_CMS_ALL_FETCHERS` (CMS_HEALTH_TYPES=hospital is now a no-op).
`is_complaint_excluded_vertical()`: **a real industry label wins** — "CHILDRESS REGIONAL MEDICAL CENTER DIALYSIS" is a Dialysis Center (proven 3.1% vertical) and "GUARDIAN REHABILITATION HOSPITAL" is a CMS Nursing Facility, so both stay. The company-name test only runs when industry is blank or Apollo's catch-all `hospital & health care` bucket (which holds home care, dental, even software). Regexes are `\b`-anchored so **hospitality** (hotels/clubs/venues — a real vertical) is never caught.
Both complaint Slack cards carry a `COMPLAINT_CALL_GUIDANCE` field — one glanceable sentence giving Cristine the opener ("saw a recent review mentioning cleanliness — do you have a cleaning team in now, or is that handled in-house?"), because a complaint lead without that context gets opened cold.

`dayPlanRungs()` in App.jsx defines Eric's operating order, surfaced as the 📋 Day Plan dashboard card AND powering "Who's next?": 0) ⭐ Eric's warm list (`isWarmList`) → 1) 📨 inquiries/replies (`lastReplyDate()` parses the poller's "📧 Reply (date…" note stamp; pending = no call since the reply) → 2) 🚶 walkthrough follow-ups → 3) ⏰ due callbacks not tried today → 4) 🚨 complaint list ([INTENT:health_violation]/[INTENT:cleanliness], <4 calls, not called today, score-sorted) → fresh queue. The card always renders all rungs (zeros dimmed) so the order itself is the training. Review-scan (`POST /api/enrich/reviews`, admin, ~$0.05/lead, 100/run cap) stamps the cleanliness tag — run small batches over undialed NPI/OSM stock to feed rung 4.

## QoL features (2026-06)
- Caller: ⚡ one-tap "No answer" (lead rows + dialer), 📞 "Who's next?" header button (due callbacks → warm → fresh), year-typo date guard (`confirmFarDate`), end-of-shift recap on sign-out, mid-shift due-callback nudge (max 1/2h).
- Admin: 🕐 Clock in/out header button + `POST /api/auth/clock-in|clock-out` (user_sessions); weekly digest includes hours/caller via shared `_compute_hours()`.
- Slack appointment approve: new pending appointment pings Slack with a one-click approve link → `GET /appt-approve?t=<signed JWT>` renders a confirm page, `POST /appt-approve` approves + fires the Angelo handoff. GET is side-effect-free so Slack link-prefetch can't auto-approve.
- Slack email queue: `run_email_queue_nudge_if_due()` (bg loop, once per ET day post-shift, `EMAIL_QUEUE_NUDGE_ENABLED`) pings when tried-to-call leads are email-eligible → `GET/POST /email-queue?t=<JWT>` review page sends ≤50 via `campaigns_batch_send` (same prefetch-safe pattern). `_get_campaign_eligible()` shared with `/api/admin/campaigns/eligible`.
- Email replies: IMAP poller writes `email_reply` audit rows + fires an instant Slack ping per matched reply (sentiment + snippet). Daily digest shows reply count + companies; weekly digest shows replies w/ WoW arrow. Requires IMAP_SERVER/IMAP_USERNAME/IMAP_PASSWORD env.
- EmailModal script presets (2026-08): three built-in scripts — ✉️ "Asked for info" (qualified/interested prospect requested an email), 🤝 "We spoke" (original post-call thank-you), 📵 "Missed call". Default picked from lead.status (interested/callback/converted → asked, no_answer → missed, else spoke). Qualified Leads cards have a Send Email icon button (lead join now includes `email`).
- Daily digest "⭐ Qualified highlights": today's interested/callback/converted or qualified calls WITH notes, excluding leads whose callbackDate is > `DIGEST_ACTIONABLE_MAX_DAYS` (default 90) out. `_ai_action_snippets()` (INSIGHTS_MODEL→Haiku) writes owner-action one-liners ("Arizona Hospital — interested, send email; needs 5x/week, URGENT") preserving urgency/frequency/sqft/budget details; hard gate = exactly one line per item or it falls back to raw note snippets (never silently drops an update). `GET /api/daily-summary?preview=1` returns the assembled sections without posting — use it to verify the AI output.

## Appointments (sales → fulfillment loop)
- LeadFlow is the system of record. Stored as JSON in `app_settings` (`appt_<leadId>`) — no DDL. Stages: pending → approved → confirmed → won/lost.
- `POST /api/appointments/{lead_id}` (any caller — books a walkthrough, starts 'pending'), `GET /api/appointments` (admin board), `POST /api/appointments/{lead_id}/transition` (admin — 'approved' fires the Angelo Slack handoff; 'confirmed'/'won'/'lost' post a walkthrough update to the Eric+Angelo channel via `_notify_walkthrough_update`), `DELETE /api/appointments/{lead_id}` (admin — cancel).
- **Walkthrough follow-ups (2026-08):** `GET /api/appointments/followups` (any caller) = appts with stage approved/confirmed whose date has passed with no won/lost decision (`_get_walkthrough_followups`). Frontend fetches every 30 min → top-priority in "Who's next?" (outranks due callbacks), own 🚶 bucket at the top of the Follow-Ups tab, and mid-shift nudge mention. `run_walkthrough_followup_nudge_if_due()` (bg loop, once per ET day, cooldown `last_walkthrough_nudge`, env `WALKTHROUGH_FOLLOWUP_NUDGE_ENABLED`) pings the ANGELO_SLACK_WEBHOOK_URL channel with the awaiting-decision list.
- **Walkthrough client relay (2026-08, HIGH LEVERAGE):** every `POST /api/calls` on a lead that has an `appt_*` record fires `_notify_walkthrough_client_call()` → instant ping to the Eric+Angelo channel with outcome (interested/callback+date/converted/not_interested), cleaned notes, qual. Skips only routine no-answer with empty notes. Exists because caller updates on live deals were sitting unseen in-app; never let this path break the call save (wrapped try/except).
- **Walkthroughs tab (2026-08):** all-users nav section (`WalkthroughsPanel`) = the post-walkthrough workspace. `GET /api/appointments/attended` (any caller) returns attended appts (date passed, stage pending/approved/confirmed — attendance is the DATE passing, not admin approval) + joined lead. Cards show needs-call-today vs called-today, sort needs-call → oldest walkthrough; 📞 Log Call for callers, Won/Lost/Note admin-only (transition endpoint is admin). Banner reminds that logged calls auto-relay to the chat. `GET /api/admin/walkthrough-channel-test` posts a test ping through the walkthrough hook + reports whether ANGELO_SLACK_WEBHOOK_URL is set.
- Caller captures it in the CallModal when marking Interested/Converted (date + area). Admin works it on the **Appointments** board (admin-only nav). Calendar / Twilio SMS to subs / Notion sub-matching are the planned next layers that hang off this.

## Note Intelligence (Haiku)
- `POST /api/notes/analyze` `{note, company?, status?}` → `{sentiment: warm|neutral|cold, outcome, callbackDate (ISO, parsed from plain English), summary, engine}`.
- Sentiment is persisted in the lead's `notes` as a `[sent:warm|neutral|cold]` tag (same tokenized-notes pattern as `[INTENT:*]` — no schema change). `cleanNote()` strips it; `parseSentiment()` reads it; `<SentimentDot/>` renders the colored dot.
- Caller UI: CallModal + My Week have 🎤 Dictate (Web Speech API) and ✨ Smart-fill (applies suggested outcome + callback date in one tap).
- `POST /api/analytics/note-insights?days=N` (admin) → Sonnet reads recent notes (call_outcomes + leads incl. imported lists/My Week) → `{headline, objections[], timing[], segments[], opportunities[], watchouts[], engine, sample, cached}`. 30-min cache; `refresh=1` bypasses. `generate_note_insights()` + `_gather_note_records()`.
- **Pickup rate by SOURCE** (`GET /api/analytics/connectivity?days=N`, admin → `source_breakdown` + `source_trend`): the sourcing feedback loop. `/api/analytics/conversions` also slices by source but measures *conversions*, which at a ~0.2% close rate is noise; pickup is dense enough to read in days. `dead_rate` mines `DEAD_NUMBER_RE` over call notes ("disconnected", "wrong number", "not in service") — free, vs ~$0.008/number for the Twilio lookup — so stock that was never dialable is separated from stock nobody answered (both log as `no_answer`). The **month trend is the point**: a blended lifetime rate hides a source whose stock has gone stale. Surfaced in ConnectivityPanel under the by-state table. `PICKUP_OUTCOMES` there includes `interested_no_dm` (a human answered, just not the DM) so the heatmap agrees with the leaderboard and receptivity index.
- **Receptivity Index** (`GET /api/analytics/receptivity?days=N`, admin): composite of contact-rate + engagement-rate (0–100) — dense signal so slices stay significant despite the ~0.2% close rate. Returns `by_industry / by_dow / by_hour / by_month / by_industry_month` + `overall`. `_agg_recept()`, CONTACT_OUTCOMES/ENGAGED_OUTCOMES. ReceptivityPanel in Analytics.
- **Macro backdrop** (`GET /api/analytics/macro`, admin): live FRED series via the public CSV export (`fredgraph.csv?id=…`, NO API key) — UNRATE/FEDFUNDS/DGS10/PAYEMS/UMCSENT. `run_macro_snapshot_if_due()` banks one snapshot/month into `app_settings` (`macro_snapshot_YYYY-MM`) so a paired macro×receptivity history accumulates for later correlation (Phase 3). `_fetch_fred_latest()`, `get_macro_snapshot()`. Env: RECEPTIVITY_TZ_OFFSET_HOURS (default -4), RECEPTIVITY_MIN_SLICE (15).
- Weekly Slack review: `GET /api/weekly-summary` posts the full Sonnet analysis + week-over-week metrics. Auto-fires once per ISO week (on/after `WEEKLY_DIGEST_DAY`) from `_bg_maintenance_loop` via `run_weekly_digest_if_due()` (cooldown row `last_weekly_digest`). The daily digest stays lean (keyword themes + trend only).
