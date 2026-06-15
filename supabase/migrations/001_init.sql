-- 큐닷 AX 파트너 상품 수집 — 초기 스키마
-- Supabase SQL Editor 또는 supabase db push로 실행

-- ── 크롤링 실행 이력 ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS crawl_runs (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    store_url       TEXT        NOT NULL,
    crawler_type    TEXT        NOT NULL,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    total_discovered INTEGER    DEFAULT 0,
    total_attempted  INTEGER    DEFAULT 0,
    total_crawled    INTEGER    DEFAULT 0,
    total_normalized INTEGER    DEFAULT 0,
    failed_count     INTEGER    DEFAULT 0,
    partial_count    INTEGER    DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── 정규화된 상품 (PartnerProductCreateInput) ──────────────────────
CREATE TABLE IF NOT EXISTS partner_products (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    source_url      TEXT        NOT NULL UNIQUE,   -- 상품 상세 URL (dedup 기준)
    store_url       TEXT        NOT NULL,           -- 수집 스토어 URL
    name            TEXT,
    image_url       TEXT,
    brand_name      TEXT,
    option1         TEXT,
    option2         TEXT,
    consumer_price  INTEGER     CHECK (consumer_price >= 0),
    sales_price     INTEGER     CHECK (sales_price >= 0),
    lowest_price    INTEGER     CHECK (lowest_price >= 0),
    discount_rate   NUMERIC(6,2),
    hashtags        TEXT[]      DEFAULT '{}',
    usp             TEXT,
    category_group  TEXT[]      DEFAULT '{}',
    status          TEXT        NOT NULL DEFAULT 'partial'
                                CHECK (status IN ('normalized', 'partial')),
    ai_fields       TEXT[]      DEFAULT '{}',
    missing_reasons JSONB       DEFAULT '{}',
    raw_evidence    JSONB       DEFAULT '{}',
    field_errors    JSONB       DEFAULT '{}',
    crawled_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 재크롤 시 updated_at 자동 갱신
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER partner_products_updated_at
    BEFORE UPDATE ON partner_products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 조회 성능용 인덱스
CREATE INDEX IF NOT EXISTS idx_partner_products_store_url
    ON partner_products (store_url);

CREATE INDEX IF NOT EXISTS idx_partner_products_status
    ON partner_products (status);

CREATE INDEX IF NOT EXISTS idx_crawl_runs_store_url
    ON crawl_runs (store_url, created_at DESC);
