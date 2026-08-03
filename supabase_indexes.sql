-- LeadFlow — recommended Supabase indexes (2026-08 performance audit)
-- Run once in the Supabase SQL editor (Dashboard → SQL → New query → paste → Run).
-- All are IF NOT EXISTS, safe to re-run. pg_trgm powers the %term% ilike search.

create extension if not exists pg_trgm;

-- call_outcomes: every hot path filters this table
create index if not exists idx_calls_calledat        on call_outcomes ("calledAt" desc);
create index if not exists idx_calls_lead_calledat   on call_outcomes ("leadId", "calledAt" desc);
create index if not exists idx_calls_caller_calledat on call_outcomes ("calledBy", "calledAt" desc);
create index if not exists idx_calls_notes_trgm      on call_outcomes using gin (notes gin_trgm_ops);

-- leads
create index if not exists idx_leads_status       on leads (status);
create index if not exists idx_leads_callback     on leads ("callbackDate") where "callbackDate" is not null and "callbackDate" <> '';
create index if not exists idx_leads_assigned     on leads ("assignedTo");
create index if not exists idx_leads_smart_sort   on leads (total_calls asc nulls first, last_called_at asc nulls first, score desc);
create index if not exists idx_leads_company_trgm on leads using gin (company gin_trgm_ops);
create index if not exists idx_leads_notes_trgm   on leads using gin (notes gin_trgm_ops);
create index if not exists idx_leads_phone_trgm   on leads using gin (phone gin_trgm_ops);

-- audit_log: emailed-flags badge + campaign suppression + reply counts
create index if not exists idx_audit_action_created on audit_log (action, created_at desc);
create index if not exists idx_audit_resource       on audit_log (resource_id, action, created_at desc);
