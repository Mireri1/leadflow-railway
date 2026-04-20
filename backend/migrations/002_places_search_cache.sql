-- Google Places Text Search cache. Shared schema with vlm-scraper and
-- recruitnil-scraper (but LeadFlow lives in a different Supabase project,
-- ucpwpjokyconwzwqvdad, so this migration needs to run here separately).
--
-- A fresh (pipeline='leadflow', city, keyword) row means we already made
-- that paid Text Search call within PLACES_CACHE_TTL_DAYS, so skip it.
-- See places_cache_load / places_cache_write in backend/main.py.

CREATE TABLE IF NOT EXISTS places_search_cache (
  pipeline         TEXT        NOT NULL,
  city             TEXT        NOT NULL,
  keyword          TEXT        NOT NULL,
  last_searched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  place_ids        TEXT[]      NOT NULL DEFAULT '{}',
  result_count     INTEGER     NOT NULL DEFAULT 0,
  PRIMARY KEY (pipeline, city, keyword)
);

CREATE INDEX IF NOT EXISTS idx_places_search_cache_last_searched_at
  ON places_search_cache (last_searched_at DESC);
