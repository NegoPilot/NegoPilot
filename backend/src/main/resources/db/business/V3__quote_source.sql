-- 관리자가 견적을 추가한다(#250). 기존 견적은 모두 합성 자료라 MOCK이고, 관리자 입력은 MANUAL이다.
-- Existing data only. 견적 행은 지우거나 고치지 않는다.
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'MOCK' CHECK (source IN ('MOCK', 'MANUAL'));
