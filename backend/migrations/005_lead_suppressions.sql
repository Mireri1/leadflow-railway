-- Global outbound suppression list. Any email on this table is excluded
-- from send_lead_to_campaign() regardless of campaign or sequence step.
-- Populated by:
--   1. IMAP poller — when a reply is classified as negative ("not
--      interested", "stop", "not now", "please remove", etc.)
--   2. vlm-scraper — cross-system push when the scraper sees a negative
--      reply, so both outreach systems share the same do-not-contact set
--   3. Manual admin add (Supabase UI / SQL) for one-off escalations
--
-- Lookup is fast (PK on email). Reason and source are diagnostic only —
-- the only thing the send path cares about is whether a row exists.

CREATE TABLE IF NOT EXISTS lead_suppressions (
  email      TEXT        PRIMARY KEY,
  reason     TEXT        NOT NULL DEFAULT 'negative_reply',
  source     TEXT,
  notes      TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
