<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { session, sessionError, refreshSession, signOut } from '@/stores/session'
import { loadSignupDraft } from '@/stores/signup'
import { describeError } from '@/utils/errors'

const draft = loadSignupDraft()
const user = computed(() => session.value ?? draft)
const router = useRouter()
const busy = ref(false)
const notice = ref<string | null>(null)
async function checkApproval() {
  busy.value = true
  const current = await refreshSession()
  if (current?.status === 'ACTIVE') await router.push({ name: 'chat' })
  else notice.value = sessionError.value ? describeError(sessionError.value).message : current ? '아직 관리자 승인 대기 중입니다.' : '로그인 후 승인 상태를 확인해 주세요.'
  busy.value = false
}
async function logout() {
  try { await signOut(); await router.push({ name: 'login' }) }
  catch (error) { notice.value = describeError(error).message }
}
</script>

<template>
  <section class="panel card" aria-labelledby="pending-h">
    <template v-if="user">
      <h1 id="pending-h">가입 승인을 기다리고 있습니다.</h1>
      <p class="lead">관리자가 가입 정보를 확인한 뒤 승인합니다. 승인 후 AI 대화와 개인 기록을 이용할 수 있습니다.</p>
      <span class="badge badge-warning">{{ session ? '승인 대기' : '가입 신청 접수' }}</span>
      <dl class="kv"><dt>이름 / 부서</dt><dd>{{ user.name }} / {{ user.department }}</dd><dt>이메일</dt><dd>{{ user.email }}</dd></dl>
      <template v-if="session">
        <button class="btn btn-primary wide" type="button" :disabled="busy" @click="checkApproval">승인 상태 확인</button>
        <button class="btn wide" type="button" @click="logout">로그아웃</button>
      </template>
      <RouterLink v-else :to="{ name: 'login' }" class="btn btn-primary wide">로그인해서 상태 확인</RouterLink>
      <p class="footnote">승인이 지연되면 관리자에게 문의해 주세요.</p>
    </template>
    <template v-else>
      <h1 id="pending-h">로그인해서 가입 상태를 확인해 주세요.</h1>
      <RouterLink :to="{ name: 'login' }" class="btn btn-primary wide">로그인</RouterLink>
      <RouterLink :to="{ name: 'signup' }" class="edit">회원가입</RouterLink>
    </template>
    <p v-if="notice" role="status">{{ notice }}</p>
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
