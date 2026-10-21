-- Existing business data only. This migration never runs schema.sql or seed.py.
ALTER TABLE suppliers DROP CONSTRAINT IF EXISTS suppliers_source_check;
ALTER TABLE suppliers ADD CONSTRAINT suppliers_source_check CHECK (source IN ('DART', 'MOCK', 'MANUAL'));
ALTER TABLE suppliers
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
    ADD COLUMN IF NOT EXISTS profile_origin_source TEXT NOT NULL DEFAULT 'MANUAL' CHECK (profile_origin_source IN ('DART', 'MOCK', 'MANUAL')),
    ADD COLUMN IF NOT EXISTS certification_source TEXT NOT NULL DEFAULT 'MOCK' CHECK (certification_source IN ('MOCK', 'MANUAL')),
    ADD COLUMN IF NOT EXISTS capability_source TEXT NOT NULL DEFAULT 'MOCK' CHECK (capability_source IN ('MOCK', 'MANUAL')),
    ADD COLUMN IF NOT EXISTS quality_source TEXT NOT NULL DEFAULT 'MOCK' CHECK (quality_source IN ('MOCK', 'MANUAL')),
    ADD COLUMN IF NOT EXISTS profile_original JSONB,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

UPDATE suppliers s SET profile_origin_source = source,
    profile_original = jsonb_build_object('source', source, 'corp_code', corp_code,
        'stock_code', stock_code, 'induty_code', induty_code, 'name', name, 'region', region,
        'employees', employees, 'revenue_eok', revenue_eok, 'founded_year', founded_year,
        'biz_no_masked', biz_no_masked)
WHERE profile_original IS NULL;

CREATE SEQUENCE IF NOT EXISTS supplier_manual_id_seq;
SELECT setval('supplier_manual_id_seq',
    GREATEST((SELECT last_value + CASE WHEN is_called THEN 1 ELSE 0 END FROM supplier_manual_id_seq),
    COALESCE((SELECT MAX(substring(supplier_id FROM 5)::BIGINT)
        FROM suppliers WHERE supplier_id ~ '^SUP-[0-9]{1,18}$'), 0) + 1, 1), false);

CREATE TABLE IF NOT EXISTS supplier_change_events (
    event_id BIGSERIAL PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
    actor_user_id UUID NOT NULL,
    action TEXT NOT NULL,
    before_data JSONB,
    after_data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS supplier_change_events_lookup ON supplier_change_events(supplier_id, event_id DESC);
CREATE INDEX IF NOT EXISTS supplier_status_source_lookup ON suppliers(status, source, supplier_id);

-- Keep all existing view columns in their original order. Append management fields.
CREATE OR REPLACE VIEW supplier_overview AS
SELECT s.supplier_id, s.source, s.corp_code, s.stock_code, s.induty_code, s.name, s.region,
       s.employees, s.revenue_eok, s.founded_year, s.biz_no_masked, s.iatf16949, s.iso14001,
       q.defect_ppm, q.on_time_rate, q.audit_grade, q.years_dealing,
       c.payment_days, c.price_linkage, c.raw_material_ratio, c.auto_renewal,
       f.bsns_year AS fin_year,
       ROUND(f.operating_income::numeric / NULLIF(f.revenue, 0), 3) AS operating_margin,
       ROUND(f.total_liabilities::numeric / NULLIF(f.total_equity, 0), 2) AS debt_ratio,
       s.status, s.certification_source, s.capability_source, s.quality_source
FROM suppliers s
LEFT JOIN quality q USING (supplier_id)
LEFT JOIN contracts c USING (supplier_id)
LEFT JOIN LATERAL (
    SELECT * FROM financials fi WHERE fi.supplier_id = s.supplier_id
    ORDER BY bsns_year DESC LIMIT 1
) f ON TRUE
WHERE s.status = 'ACTIVE';
