-- Decision-maker direct line.
--
-- The 2026-10 cold-call script's first step on a no-name lead is just
-- "who handles facilities?" — the name and, when offered, their direct line
-- or cell are the asset that call produces. The name already has a home
-- (leads.firstName / lastName / title). The direct line did not: `phone` is
-- the switchboard, is UNIQUE (007) and is what the dialer queue, dedupe and
-- the Twilio bridge key on, so it cannot double as the DM's cell.
--
-- Frontend sends dm_phone in its OWN PATCH, separate from the call-outcome
-- save, so a missing column can only fail the direct-line save (and says so
-- to the caller) — never the call log. Run this before expecting direct
-- lines to persist.
alter table leads add column if not exists dm_phone text;
comment on column leads.dm_phone is
  'Decision-maker direct line / cell, captured by the caller. NOT the switchboard (phone). Not unique.';
