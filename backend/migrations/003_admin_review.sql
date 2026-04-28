-- Admin review state for qualified calls. Lets Eric (or any admin) mark
-- each qualification as approved/rejected so callers get feedback in the
-- Qualified Leads tab. Run this once in the LeadFlow Supabase
-- (project: ucpwpjokyconwzwqvdad) SQL editor.

ALTER TABLE call_outcomes
  ADD COLUMN IF NOT EXISTS admin_review        TEXT,
  ADD COLUMN IF NOT EXISTS admin_reviewed_by   TEXT,
  ADD COLUMN IF NOT EXISTS admin_reviewed_at   TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_call_outcomes_admin_review
  ON call_outcomes(admin_review);
