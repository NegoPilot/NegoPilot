-- 재검사에서 Agent가 고친 조건과 맞지 않는 인사말을 다시 쓰면 그 문장을 협상안의 새 버전으로 저장한다 (#306).
-- 이 버전의 출처는 COVER_SYNC다. 사용자가 고친 버전(USER_EDIT)과 구분해 변경 이력에서 알아볼 수 있게 한다.
ALTER TABLE neg_proposal_revisions DROP CONSTRAINT neg_proposal_revisions_origin_check;
ALTER TABLE neg_proposal_revisions ADD CONSTRAINT neg_proposal_revisions_origin_check
    CHECK (origin IN ('GENERATED', 'USER_EDIT', 'COVER_SYNC'));
