-- Apollo per-company enrichment cache. Survives Railway redeploys so we
-- don't re-spend ~1 credit on companies we already looked up. Mirrors the
-- in-process _apollo_enrich_cache dict in backend/main.py.
--
-- A row with person=NULL is a cached MISS (Apollo had no DM for this
-- company). expires_at is set on insert based on APOLLO_ENRICH_CACHE_TTL_HOURS.

CREATE TABLE IF NOT EXISTS apollo_enrich_cache (
  company_key TEXT        PRIMARY KEY,
  person      JSONB,
  expires_at  TIMESTAMPTZ NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_apollo_enrich_cache_expires
  ON apollo_enrich_cache (expires_at);
