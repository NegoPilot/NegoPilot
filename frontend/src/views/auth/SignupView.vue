<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { signup } from '@/api/auth'
import { saveSignupDraft } from '@/stores/signup'
import { describeError } from '@/utils/errors'
import { isEmail, passwordProblem } from '@/utils/validation'

const router = useRouter()
const form = reactive({ name: '', department: '', position: '', email: '', password: '', passwordConfirm: '' })
const tried = ref(false)
const busy = ref(false)
const notice = ref<string | null>(null)
const errors = computed(() => {
  const result: Record<string, string> = {}
  if (!form.name.trim() || [...form.name.trim()].length > 100) result.name = '이름을 100자 이내로 입력해 주세요.'
  if (!form.department.trim() || [...form.department.trim()].length > 100) result.department = '부서를 100자 이내로 입력해 주세요.'
  if ([...form.position].length > 100) result.position = '직책은 100자 이내로 입력해 주세요.'
  if (!isEmail(form.email) || form.email.trim().length > 254) result.email = '이메일 형식을 확인해 주세요.'
  const password = passwordProblem(form.password)
  if (password) result.password = password
  if (form.password !== form.passwordConfirm || !form.passwordConfirm) result.passwordConfirm = '비밀번호가 서로 다릅니다.'
  return result
})
const shown = computed(() => tried.value ? errors.value : {})
async function submit() {
  if (busy.value) return
  tried.value = true
  notice.value = null
  const first = Object.keys(errors.value)[0]
  if (first) { document.getElementById(`s-${first}`)?.focus(); return }
  busy.value = true
  try {
    const user = await signup({ name: form.name.trim(), department: form.department.trim(), job_title: form.position.trim(), email: form.email.trim(), password: form.password })
    saveSignupDraft({ name: user.name, department: user.department, position: user.job_title, email: user.email })
    await router.push({ name: 'signup-pending' })
  } catch (error) { notice.value = describeError(error).message }
  finally { busy.value = false; form.password = ''; form.passwordConfirm = '' }
}
</script>

<template>
  <section class="panel card" aria-labelledby="signup-h">
    <h1 id="signup-h">계정 만들기</h1>
    <p class="lead">회원 정보를 등록한 뒤 관리자의 가입 승인을 받습니다.</p>
    <form class="grid" novalidate @submit.prevent="submit">
      <div class="field">
        <label for="s-name">이름 *</label>
        <input id="s-name" v-model="form.name" autocomplete="name" maxlength="100" :aria-invalid="!!shown.name" />
        <span v-if="shown.name" class="field-error">{{ shown.name }}</span>
      </div>
      <div class="field">
        <label for="s-department">부서 *</label>
        <input id="s-department" v-model="form.department" maxlength="100" :aria-invalid="!!shown.department" />
        <span v-if="shown.department" class="field-error">{{ shown.department }}</span>
      </div>
      <div class="field span">
        <label for="s-position">직책</label>
        <input id="s-position" v-model="form.position" maxlength="100" />
        <span v-if="shown.position" class="field-error">{{ shown.position }}</span>
      </div>
      <div class="field span">
        <label for="s-email">이메일 *</label>
        <input id="s-email" v-model="form.email" type="email" autocomplete="email" maxlength="254" :aria-invalid="!!shown.email" />
        <span v-if="shown.email" class="field-error">{{ shown.email }}</span>
      </div>
      <div class="field">
        <label for="s-password">비밀번호 *</label>
        <input id="s-password" v-model="form.password" type="password" autocomplete="new-password" :aria-invalid="!!shown.password" />
        <span v-if="shown.password" class="field-error">{{ shown.password }}</span>
      </div>
      <div class="field">
        <label for="s-passwordConfirm">비밀번호 확인 *</label>
        <input id="s-passwordConfirm" v-model="form.passwordConfirm" type="password" autocomplete="new-password" :aria-invalid="!!shown.passwordConfirm" />
        <span v-if="shown.passwordConfirm" class="field-error">{{ shown.passwordConfirm }}</span>
      </div>
      <p class="footnote span">비밀번호는 영문과 숫자 및 특수문자를 포함해 8자 이상 입력해 주세요. 최대 길이는 UTF-8 기준 72바이트입니다.</p>
      <p v-if="notice" class="field-error span" role="alert">{{ notice }}</p>
      <button class="btn btn-primary span" type="submit" :disabled="busy">{{ busy ? '신청 중…' : '가입 승인 요청' }}</button>
    </form>
    <div class="bottom"><span class="muted">이미 가입하셨나요?</span><RouterLink :to="{ name: 'login' }" class="to-login">로그인</RouterLink></div>
  </section>
</template>

<style scoped>
.card {
  max-width: 488px;
  margin: 0 auto;
  padding: 26px 20px 30px;
}
.card h1 {
  font-size: 19px;
}
.lead {
  margin: 14px 0 12px;
  font-size: 11px;
  color: var(--text-2);
}
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 10px;
}
.span {
  grid-column: 1 / -1;
}
.field > label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text);
}
.with-btn {
  display: flex;
  gap: 10px;
}
.with-btn input {
  flex: 1;
  min-width: 0;
}
.with-btn .btn {
  flex: 0 0 auto;
  min-width: 115px;
  height: 38px;
  font-size: 11px;
  font-weight: 600;
}
.hint {
  font-size: 11px;
  color: var(--primary);
}
.footnote {
  margin: 0;
}
.check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  cursor: pointer;
}
.check input {
  margin: 0;
  accent-color: var(--primary);
}
.bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding-right: 80px;
}
.to-login {
  font-size: 11px;
  font-weight: 600;
  text-decoration: none;
}
@media (max-width: 520px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .bottom {
    padding-right: 0;
  }
}
</style>
