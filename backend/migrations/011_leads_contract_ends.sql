-- Contract renewal date, from the step-2 answer.
--
-- "Are you under contract with someone right now, or handling it in-house?"
-- The most common real answer is "under contract until March". That used to
-- be logged as Not Interested and lost. The caller now records the month;
-- the app books a callback ~75 days before it and keeps the lead in the
-- pipeline (status callback), so today's "no" becomes a dated renewal.
--
-- Stored as the FIRST of the month (the caller picks a month, not a day).
-- Written in the same separate PATCH as dm_phone / reach_notes, so a missing
-- column fails only this field, loudly — never the call log.
alter table leads add column if not exists contract_ends date;
comment on column leads.contract_ends is
  'Month the prospect''s current cleaning contract ends (stored as the 1st). Drives the renewal callback.';
