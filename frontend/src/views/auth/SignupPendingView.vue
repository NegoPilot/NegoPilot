<script setup lang="ts">
import { RouterLink } from 'vue-router'

import { DEFAULT_SIGNUP_ROLE_LABEL, ORGANIZATION } from '@/data/accounts'
import { loadSignupDraft } from '@/stores/signup'

const draft = loadSignupDraft()
const company = draft
  ? draft.companyCode.toUpperCase() === ORGANIZATION.code
    ? ORGANIZATION.name
    : `회사 코드 ${draft.companyCode} (확인 중)`
  : ''
</script>

<template>
  <section class="panel card" aria-labelledby="pending-h">
    <template v-if="draft">
      <h1 id="pending-h">가입 신청이 접수되었습니다.</h1>
      <p class="lead">관리자가 소속과 신청 정보를 확인하고 있습니다.<br />승인 결과는 회사 이메일로 안내해 드립니다.</p>
      <span class="badge badge-warning">승인 대기</span>

      <dl class="kv">
        <dt>이름 / 부서</dt><dd>{{ draft.name }} / {{ draft.department }}</dd>
        <dt>소속 회사</dt><dd>{{ company }}</dd>
        <dt>회사 이메일</dt><dd>{{ draft.email }}</dd>
        <dt>신청 역할</dt><dd>{{ DEFAULT_SIGNUP_ROLE_LABEL }}</dd>
      </dl>

      <p class="next">승인 후 로그인하면 RFQ 조회와 협력사 검토를 시작할 수 있습니다.</p>
      <RouterLink :to="{ name: 'login' }" class="btn btn-primary wide">로그인 화면으로</RouterLink>
      <RouterLink :to="{ name: 'signup' }" class="edit">신청 정보 수정하기</RouterLink>
      <p class="footnote">승인이 지연되면 소속 회사 관리자에게 문의해 주세요.</p>
    </template>

    <template v-else>
      <h1 id="pending-h">접수된 가입 신청이 없습니다.</h1>
      <p class="lead">이 브라우저 탭에서 신청한 내용만 확인할 수 있습니다. 회원가입을 먼저 진행해 주세요.</p>
      <RouterLink :to="{ name: 'signup' }" class="btn btn-primary wide">회원가입으로 이동</RouterLink>
      <RouterLink :to="{ name: 'login' }" class="edit">로그인 화면으로</RouterLink>
    </template>
  </section>
</template>

<style scoped>
.card {
  max-width: 460px;
  margin: 20px auto;
  padding: 28px 26px 30px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.card h1 {
  font-size: 19px;
}
.lead {
  margin: 16px 0 14px;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-2);
}
.kv {
  align-self: stretch;
  margin: 14px 0 0;
  padding: 14px 0;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 12px 0;
  font-size: 11px;
}
.kv dt {
  color: var(--text-3);
}
.kv dd {
  margin: 0;
}
.next {
  margin: 12px 0;
  font-size: 11px;
}
.wide {
  align-self: stretch;
}
.edit {
  align-self: center;
  margin: 18px 0;
  font-size: 11px;
  font-weight: 600;
  text-decoration: none;
}
.footnote {
  margin: 0;
}
</style>
