-- 008 — Owned call transcription + call intelligence (Deepgram → Claude)
--
-- RUN THIS IN THE SUPABASE SQL EDITOR BEFORE setting TRANSCRIBE_ENABLED=true.
-- The pipeline ships behind that kill switch precisely so this migration can
-- land first: every write below targets columns/tables that do not exist yet,
-- and Supabase rejects an ENTIRE insert that names an unknown column.
--
-- DEVIATION FROM THE SPEC, deliberately:
--   The spec says `alter table calls ...` and `call_id uuid references calls(id)`.
--   There is no `calls` table. Calls live in `call_outcomes` (CLAUDE.md is
--   explicit: "Table: call_outcomes (NOT calls)") and its `id` is an integer,
--   not a uuid — logged calls read id=14365. So the foreign keys below are
--   bigint → call_outcomes(id). Using the spec's DDL verbatim would fail on
--   the first statement.

-- ── call_outcomes: per-call pipeline state ──────────────────────────────────
alter table call_outcomes add column if not exists answered_by text;
  -- human | machine_start | machine_end_beep | machine_end_silence
  -- | machine_end_other | fax | unknown   (Twilio AMD)
alter table call_outcomes add column if not exists recording_sid text;
alter table call_outcomes add column if not exists recording_duration_sec int;
alter table call_outcomes add column if not exists transcription_status text default 'none';
  -- none | skipped | queued | processing | done | failed
alter table call_outcomes add column if not exists transcription_skip_reason text;
alter table call_outcomes add column if not exists disposition_mismatch boolean default false;

create index if not exists idx_call_outcomes_recording_sid
  on call_outcomes(recording_sid);
create index if not exists idx_call_outcomes_transcription_status
  on call_outcomes(transcription_status);

-- ── jobs: the queue ─────────────────────────────────────────────────────────
-- The spec says "do not add a new infra dependency". LeadFlow already runs a
-- background thread (_bg_maintenance_loop), so the queue is a table that loop
-- polls — no pg_cron, no broker.
create table if not exists jobs (
  id          bigserial primary key,
  kind        text        not null,                    -- transcribe_call | analyze_call
  payload     jsonb       not null default '{}'::jsonb,
  dedupe_key  text,                                    -- natural idempotency key
  status      text        not null default 'queued',   -- queued|running|done|failed
  attempts    int         not null default 0,
  run_after   timestamptz not null default now(),
  last_error  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists idx_jobs_claimable on jobs(status, run_after);
-- One live job per (kind, dedupe_key). Twilio retries recording-status
-- deliveries, so a duplicate webhook must not enqueue a second transcription.
create unique index if not exists idx_jobs_dedupe_live
  on jobs(kind, dedupe_key) where status in ('queued', 'running');

-- ── call_transcripts ────────────────────────────────────────────────────────
create table if not exists call_transcripts (
  id             bigserial primary key,
  call_id        bigint      references call_outcomes(id) on delete cascade,
  lead_id        bigint,                                -- denormalised for search filters
  recording_sid  text        not null unique,           -- idempotency key
  provider       text        not null default 'deepgram',
  model          text        not null,
  language       text        default 'en-US',
  duration_sec   numeric,
  full_text      text        not null,                  -- speaker-labelled, timestamped
  utterances     jsonb       not null default '[]'::jsonb,
  raw            jsonb,                                 -- provider response, words[] trimmed
  cost_usd       numeric(8,5),
  created_at     timestamptz not null default now()
);
create index if not exists idx_call_transcripts_call on call_transcripts(call_id);
create index if not exists idx_call_transcripts_lead on call_transcripts(lead_id);
create index if not exists idx_call_transcripts_fts
  on call_transcripts using gin (to_tsvector('english', full_text));

-- ── call_analyses ───────────────────────────────────────────────────────────
create table if not exists call_analyses (
  id                     bigserial primary key,
  call_id                bigint  references call_outcomes(id) on delete cascade,
  transcript_id          bigint  references call_transcripts(id) on delete cascade,
  lead_id                bigint,
  model                  text    not null,
  summary                text    not null,
  disposition            text    not null,
  disposition_confidence numeric(3,2),
  caller_disposition     text,                          -- what the rep logged, for the mismatch view
  next_step              text,
  callback_at            timestamptz,
  objections             jsonb   not null default '[]'::jsonb,
  prospect_sentiment     text,                          -- positive | neutral | negative
  decision_maker_reached boolean,
  qa                     jsonb   not null default '{}'::jsonb,
  flags                  text[]  default '{}',
  cost_usd               numeric(8,5),
  created_at             timestamptz not null default now()
);
create index if not exists idx_call_analyses_call on call_analyses(call_id);
create index if not exists idx_call_analyses_lead on call_analyses(lead_id);
create index if not exists idx_call_analyses_disposition on call_analyses(disposition);
create index if not exists idx_call_analyses_flags on call_analyses using gin (flags);
create index if not exists idx_call_analyses_created on call_analyses(created_at desc);
