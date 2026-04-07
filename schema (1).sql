-- ─────────────────────────────────────────────────────────────────────────────
-- LeadFlow — Supabase Schema
-- Paste this entire file into: Supabase Dashboard → SQL Editor → Run
-- ─────────────────────────────────────────────────────────────────────────────

-- Leads table
create table if not exists leads (
  id             bigserial primary key,
  "firstName"    text default '',
  "lastName"     text default '',
  title          text default '',
  company        text default '',
  industry       text default '',
  phone          text default '',
  email          text default '',
  website        text default '',
  address        text default '',
  city           text default '',
  state          text default '',
  status         text default 'new',
  "assignedTo"   text default '',
  notes          text default '',
  source         text default '',
  score          int  default 0,
  "callbackDate" text default '',
  "createdAt"    timestamptz default now(),
  "updatedAt"    timestamptz default now(),
  "createdBy"    text default ''
);

-- Call outcomes table
create table if not exists call_outcomes (
  id             bigserial primary key,
  "leadId"       bigint references leads(id) on delete cascade,
  outcome        text not null,
  notes          text default '',
  duration       int  default 0,
  "callbackDate" text default '',
  "calledAt"     timestamptz default now(),
  "calledBy"     text default ''
);

-- Indexes for fast filtering
create index if not exists idx_leads_status      on leads(status);
create index if not exists idx_leads_assigned    on leads("assignedTo");
create index if not exists idx_leads_score       on leads(score desc);
create index if not exists idx_leads_callback    on leads("callbackDate");
create index if not exists idx_leads_created     on leads("createdAt" desc);
create index if not exists idx_calls_lead        on call_outcomes("leadId");
create index if not exists idx_calls_at          on call_outcomes("calledAt" desc);

-- Row Level Security (required by Supabase)
-- We use a simple shared-password approach, so we open access to anon key
alter table leads         enable row level security;
alter table call_outcomes enable row level security;

-- Drop existing policies if re-running
drop policy if exists "open_leads"         on leads;
drop policy if exists "open_call_outcomes" on call_outcomes;

-- Allow full access via anon key (your app controls access via team password)
create policy "open_leads"
  on leads for all
  using (true)
  with check (true);

create policy "open_call_outcomes"
  on call_outcomes for all
  using (true)
  with check (true);

-- User sessions table (tracks every sign-in)
create table if not exists user_sessions (
  id         bigserial primary key,
  username   text not null,
  signed_in  timestamptz default now()
);

alter table user_sessions enable row level security;

drop policy if exists "open_user_sessions" on user_sessions;
create policy "open_user_sessions"
  on user_sessions for all
  using (true)
  with check (true);

create index if not exists idx_sessions_user on user_sessions(username);
create index if not exists idx_sessions_time on user_sessions(signed_in desc);
