-- Usage tracking per user. Every paid external API call (Google Places
-- Text Search, Details, Autocomplete) writes one row here so we can see
-- exactly who ran up the bill. Run this once in the LeadFlow Supabase
-- (project: ucpwpjokyconwzwqvdad) SQL editor.

CREATE TABLE IF NOT EXISTS usage_events (
  id           BIGSERIAL PRIMARY KEY,
  username     TEXT NOT NULL,
  -- 'google_text_search' | 'google_details' | 'google_autocomplete' | 'scrape_call'
  event_type   TEXT NOT NULL,
  cost_cents   NUMERIC(10, 4) NOT NULL DEFAULT 0,
  metadata     JSONB,
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usage_events_user_created ON usage_events(username, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_created      ON usage_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_type_created ON usage_events(event_type, created_at DESC);
