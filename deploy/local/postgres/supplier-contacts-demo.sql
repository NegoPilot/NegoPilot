-- 로컬 시연용 협력사 담당자. 예약 도메인 example.test라 실제로 배달되지 않는다.
-- 빈 DB 초기화(10-init.sh)에서만 쓴다. 운영 DB에는 넣지 않는다.
INSERT INTO supplier_contacts (supplier_id, name, email, role, is_primary)
SELECT supplier_id, name || ' 영업 담당자', 'sales.' || lower(supplier_id) || '@example.test', '영업', TRUE
FROM suppliers
ON CONFLICT DO NOTHING;
