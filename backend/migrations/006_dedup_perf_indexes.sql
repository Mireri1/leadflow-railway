-- Performance + dedup indexes.
--
-- 1. call_outcomes(leadId) — log_call now does `count=exact` on every call
--    to derive total_calls atomically (avoids the read-modify-write race
--    + the GET-failure reset bug). Without this index that count is a
--    seq scan; fine at 5K calls, slow at 100K+. Also speeds /api/calls/{lead_id}.
--
-- 2. leads(phone) — the cross-run dedup in save_to_supabase / import_leads
--    queries `phone=in.(...)` to find existing rows. Currently a seq scan.
--    Once leads(phone) is fully normalized (canonical (NNN) NNN-NNNN), the
--    follow-up migration adds a UNIQUE constraint here; this index doubles
--    as the supporting B-tree.
--
-- 3. leads(last_called_at) + leads(total_calls) — the smart-sort
--    `order=total_calls.asc.nullsfirst, last_called_at.asc.nullsfirst, score.desc`
--    needs an index to avoid sorting the whole table on every dialer load.
--
-- Run once in the LeadFlow Supabase (project: ucpwpjokyconwzwqvdad) SQL editor.

CREATE INDEX IF NOT EXISTS idx_call_outcomes_leadid
  ON call_outcomes("leadId");

CREATE INDEX IF NOT EXISTS idx_leads_phone
  ON leads(phone);

CREATE INDEX IF NOT EXISTS idx_leads_smart_sort
  ON leads(total_calls NULLS FIRST, last_called_at NULLS FIRST, score DESC);

CREATE INDEX IF NOT EXISTS idx_leads_status
  ON leads(status);
