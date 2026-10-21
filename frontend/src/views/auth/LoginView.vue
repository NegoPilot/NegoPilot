<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { safeRedirect, sessionError, signIn } from '@/stores/session'
import { describeError } from '@/utils/errors'
import { isEmail } from '@/utils/validation'

const route = useRoute()
const router = useRouter()

// '이메일 기억하기'는 이 브라우저에만 남긴다. 저장소를 못 쓰면 기억하지 않는다
const REMEMBER_KEY = 'negopilot.rememberedEmail'
function readRemembered(): string {
  try {
    return localStorage.getItem(REMEMBER_KEY) ?? ''
  } catch {
    return ''
  }
}
function writeRemembered(email: string | null) {
  try {
    if (email) localStorage.setItem(REMEMBER_KEY, email)
    else localStorage.removeItem(REMEMBER_KEY)
  } catch {
    // 무시: 다음 방문 때 다시 입력하면 된다
  }
}

const remembered = readRemembered()
const form = reactive({ email: remembered, password: '', remember: remembered !== '' })
const tried = ref(false)
const notice = ref<string | null>(null)
const busy = ref(false)
const displayedNotice = computed(() => notice.value ?? (sessionError.value ? describeError(sessionError.value).message : null))

const emailError = computed(() => {
  if (!tried.value) return null
  if (!form.email.trim()) return '회사 이메일을 입력해 주세요.'
  return isEmail(form.email) ? null : '이메일 형식을 확인해 주세요. (예: name@company.com)'
})
const passwordError = computed(() => (tried.value && !form.password ? '비밀번호를 입력해 주세요.' : null))

async function login() {
  if (busy.value) return
  tried.value = true
  notice.value = null
  if (emailError.value || passwordError.value) return
  busy.value = true
  try {
    const user = await signIn(form.email.trim(), form.password)
    writeRemembered(form.remember ? form.email.trim() : null)
    const redirect = safeRedirect(route.query.redirect)
    await router.push(user.status === 'PENDING' ? { name: 'signup-pending' } : redirect ?? { name: 'chat' })
  } catch (error) {
    notice.value = describeError(error).message
  } finally {
    busy.value = false
    form.password = ''
  }
}

function forgotPassword() {
  notice.value = '비밀번호 재설정은 소속 회사 관리자에게 요청해 주세요. 현재 비밀번호 재설정 메일은 제공하지 않습니다.'
}
</script>

<template>
  <div class="login">
    <section class="intro" aria-labelledby="intro-h">
      <p class="intro-brand">NegoPilot</p>
      <h2 id="intro-h">오늘의 구매 업무를<br />이어서 시작하세요.</h2>
      <p class="intro-sub">협력사와 RFQ, 검토 근거를<br />팀의 업무 흐름 안에서 관리합니다.</p>
      <div class="intro-new">
        <h3>처음 이용하시나요?</h3>
        <p>회사 이메일로 가입한 뒤<br />소속 관리자의 승인을 받아 주세요.</p>
        <RouterLink :to="{ name: 'signup' }" class="btn intro-btn">회원가입으로 이동 →</RouterLink>
      </div>
    </section>

    <section class="form-side" aria-labelledby="login-h">
      <h1 id="login-h">로그인</h1>
      <p class="lead">등록한 회사 이메일과 비밀번호를 입력해 주세요.</p>

      <form class="form" novalidate @submit.prevent="login">
        <div class="field">
          <label for="l-email">회사 이메일</label>
          <input
            id="l-email"
            v-model="form.email"
            type="email"
            autocomplete="username"
            placeholder="name@company.com"
            :aria-invalid="!!emailError"
            :aria-describedby="emailError ? 'l-email-error' : undefined"
          />
          <span v-if="emailError" id="l-email-error" class="field-error">{{ emailError }}</span>
        </div>

        <div class="field">
          <label for="l-password">비밀번호</label>
          <input
            id="l-password"
            v-model="form.password"
            type="password"
            autocomplete="current-password"
            placeholder="비밀번호 입력"
            :aria-invalid="!!passwordError"
            :aria-describedby="passwordError ? 'l-password-error' : undefined"
          />
          <span v-if="passwordError" id="l-password-error" class="field-error">{{ passwordError }}</span>
        </div>

        <div class="row">
          <label class="check">
            <input v-model="form.remember" type="checkbox" />
            이메일 기억하기
          </label>
          <button class="btn ghost" type="button" @click="forgotPassword">비밀번호를 잊으셨나요?</button>
        </div>
        <p v-if="displayedNotice" class="notice" role="alert">{{ displayedNotice }}</p>

        <button class="btn btn-primary wide" type="submit" :disabled="busy">{{ busy ? '로그인 중…' : '로그인' }}</button>
        <p class="footnote">로그인에 문제가 있으면 소속 관리자에게 문의해 주세요.</p>
      </form>

      <div class="row signup-row">
        <span class="muted">아직 계정이 없으신가요?</span>
        <RouterLink :to="{ name: 'signup' }" class="btn ghost">회원가입</RouterLink>
      </div>

      <p class="footnote">관리자 권한은 로그인한 계정의 서버 설정으로 확인합니다.</p>
    </section>
  </div>
</template>

<style scoped>
.login {
  max-width: 900px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 60px;
  align-items: start;
}
.intro {
  display: flex;
  flex-direction: column;
  padding: 28px 20px;
  min-height: 420px;
  background: #eceef1;
}
.intro-brand {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
}
.intro h2 {
  margin-top: 56px;
  font-size: 27px;
  line-height: 1.45;
}
.intro-sub {
  margin: 18px 0 0;
  font-size: 11px;
  color: var(--text-2);
}
.intro-new {
  margin-top: auto;
  padding-top: 18px;
  border-top: 1px solid var(--border-strong);
}
.intro-new h3 {
  font-size: 12px;
}
.intro-new p {
  margin: 14px 0;
  font-size: 11px;
  color: var(--text-2);
}
.intro-btn {
  height: 30px;
  font-size: 11px;
  font-weight: 600;
  color: var(--primary);
  border-color: var(--surface);
}
.form-side {
  padding-top: 36px;
}
.form-side h1 {
  font-size: 19px;
}
.lead {
  margin: 20px 0 14px;
  font-size: 11px;
  color: var(--text-2);
}
.form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  cursor: pointer;
}
.check input {
  margin: 0;
  accent-color: var(--primary);
}
.ghost {
  height: 30px;
  min-width: 156px;
  font-size: 11px;
  font-weight: 600;
  color: var(--primary);
  border-color: var(--surface);
}
.ghost:hover {
  border-color: var(--border);
}
.wide {
  width: 100%;
}
.notice {
  margin: -6px 0 0;
  padding: 8px 10px;
  font-size: 11px;
  color: var(--text-2);
  background: var(--primary-soft);
  border-radius: var(--radius);
}
.form .footnote {
  margin: -8px 0 0;
}
.signup-row {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.admin {
  margin-top: 36px;
}
.form-side > .footnote {
  margin: 8px 0 0;
}
.demo {
  color: var(--text-3);
}
@media (max-width: 760px) {
  .login {
    grid-template-columns: minmax(0, 1fr);
    gap: 24px;
  }
  .intro {
    min-height: 0;
  }
  .intro h2 {
    margin-top: 24px;
  }
  .intro-new {
    margin-top: 24px;
  }
}
</style>
