<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { DEFAULT_SIGNUP_ROLE_LABEL, ORGANIZATION } from '@/data/accounts'
import { useAdminStore } from '@/stores/admin'
import { loadSignupDraft, saveSignupDraft } from '@/stores/signup'
import { isEmail, isVerificationCode, passwordProblem } from '@/utils/validation'

const router = useRouter()
const admin = useAdminStore()

// '신청 정보 수정하기'로 돌아오면 이전 입력을 채운다 (비밀번호와 인증번호는 다시 받는다)
const draft = loadSignupDraft()
const form = reactive({
  name: draft?.name ?? '',
  department: draft?.department ?? '',
  companyCode: draft?.companyCode ?? '',
  position: draft?.position ?? '',
  email: draft?.email ?? '',
  code: '',
  password: '',
  passwordConfirm: '',
  agreed: false,
})

const tried = ref(false)
const codeSentTo = ref<string | null>(null)
const codeNotice = ref<string | null>(null)

// 인증번호를 받은 뒤 이메일을 바꾸면 다시 받아야 한다
watch(
  () => form.email.trim(),
  (email) => {
    if (codeSentTo.value && email !== codeSentTo.value) {
      codeSentTo.value = null
      codeNotice.value = null
    }
  },
)

const emailFormatError = computed(() => {
  if (!form.email.trim()) return '회사 이메일을 입력해 주세요.'
  return isEmail(form.email) ? null : '이메일 형식을 확인해 주세요. (예: name@company.com)'
})

const errors = computed(() => {
  const e: Partial<Record<'name' | 'department' | 'companyCode' | 'email' | 'code' | 'password' | 'passwordConfirm' | 'agreed', string>> = {}
  if (!form.name.trim()) e.name = '이름을 입력해 주세요.'
  if (!form.department.trim()) e.department = '부서를 입력해 주세요.'
  if (!form.companyCode.trim()) e.companyCode = '관리자에게 받은 회사 코드를 입력해 주세요.'
  if (emailFormatError.value) e.email = emailFormatError.value
  if (!codeSentTo.value) e.code = '인증번호 받기를 먼저 눌러 주세요.'
  else if (!isVerificationCode(form.code)) e.code = '6자리 숫자를 입력해 주세요.'
  const pw = passwordProblem(form.password)
  if (pw) e.password = pw
  if (!form.passwordConfirm) e.passwordConfirm = '비밀번호를 한 번 더 입력해 주세요.'
  else if (form.passwordConfirm !== form.password) e.passwordConfirm = '비밀번호가 서로 다릅니다.'
  if (!form.agreed) e.agreed = '필수 약관에 동의해 주세요.'
  return e
})
const shown = computed(() => (tried.value ? errors.value : {}))

function sendCode() {
  if (emailFormatError.value) {
    codeNotice.value = emailFormatError.value
    return
  }
  codeSentTo.value = form.email.trim()
  codeNotice.value = `${codeSentTo.value}로 인증번호를 보냈습니다. (시안: 아무 6자리 숫자나 입력하세요)`
}

function submit() {
  tried.value = true
  const first = Object.keys(errors.value)[0]
  if (first) {
    document.getElementById(`s-${first}`)?.focus()
    return
  }
  const known = form.companyCode.trim().toUpperCase() === ORGANIZATION.code
  const saved = {
    name: form.name.trim(),
    department: form.department.trim(),
    companyCode: form.companyCode.trim(),
    position: form.position.trim(),
    email: form.email.trim(),
  }
  saveSignupDraft(saved)
  const today = new Intl.DateTimeFormat('sv-SE').format(new Date()).replaceAll('-', '.')
  admin.submitRequest({
    name: saved.name,
    email: saved.email,
    department: saved.department,
    position: saved.position,
    company: known ? ORGANIZATION.name : `회사 코드 ${saved.companyCode}`,
    requestedAt: today,
  })
  router.push({ name: 'signup-pending' })
}

function describedBy(key: keyof typeof errors.value) {
  return shown.value[key] ? `s-${key}-error` : undefined
}
</script>

<template>
  <section class="panel card" aria-labelledby="signup-h">
    <h1 id="signup-h">팀 계정 만들기</h1>
    <p class="lead">회사 정보를 확인한 뒤 관리자에게 가입 승인을 요청합니다.</p>

    <form class="grid" novalidate @submit.prevent="submit">
      <div class="field">
        <label for="s-name">이름 *</label>
        <input id="s-name" v-model="form.name" type="text" autocomplete="name" placeholder="이름 입력" :aria-invalid="!!shown.name" :aria-describedby="describedBy('name')" />
        <span v-if="shown.name" id="s-name-error" class="field-error">{{ shown.name }}</span>
      </div>
      <div class="field">
        <label for="s-department">부서 *</label>
        <input id="s-department" v-model="form.department" type="text" placeholder="예: 구매1팀" :aria-invalid="!!shown.department" :aria-describedby="describedBy('department')" />
        <span v-if="shown.department" id="s-department-error" class="field-error">{{ shown.department }}</span>
      </div>

      <div class="field">
        <label for="s-companyCode">회사 코드 *</label>
        <input id="s-companyCode" v-model="form.companyCode" type="text" autocomplete="off" placeholder="관리자에게 받은 회사 코드" :aria-invalid="!!shown.companyCode" :aria-describedby="describedBy('companyCode')" />
        <span v-if="shown.companyCode" id="s-companyCode-error" class="field-error">{{ shown.companyCode }}</span>
      </div>
      <div class="field">
        <label for="s-position">직책</label>
        <input id="s-position" v-model="form.position" type="text" placeholder="예: 매니저" />
      </div>

      <div class="field span">
        <label for="s-email">회사 이메일 *</label>
        <div class="with-btn">
          <input id="s-email" v-model="form.email" type="email" autocomplete="email" placeholder="name@company.com" :aria-invalid="!!shown.email" :aria-describedby="describedBy('email')" />
          <button class="btn" type="button" @click="sendCode">{{ codeSentTo ? '인증번호 다시 받기' : '인증번호 받기' }}</button>
        </div>
        <span v-if="shown.email" id="s-email-error" class="field-error">{{ shown.email }}</span>
        <span v-else-if="codeNotice" class="hint" role="status">{{ codeNotice }}</span>
      </div>

      <div class="field span">
        <label for="s-code">이메일 인증번호 *</label>
        <input id="s-code" v-model="form.code" type="text" inputmode="numeric" maxlength="6" autocomplete="one-time-code" placeholder="6자리 인증번호 입력" :aria-invalid="!!shown.code" :aria-describedby="describedBy('code')" />
        <span v-if="shown.code" id="s-code-error" class="field-error">{{ shown.code }}</span>
      </div>

      <div class="field">
        <label for="s-password">비밀번호 *</label>
        <input id="s-password" v-model="form.password" type="password" autocomplete="new-password" placeholder="영문·숫자·특수문자 8자 이상" :aria-invalid="!!shown.password" :aria-describedby="describedBy('password')" />
        <span v-if="shown.password" id="s-password-error" class="field-error">{{ shown.password }}</span>
      </div>
      <div class="field">
        <label for="s-passwordConfirm">비밀번호 확인 *</label>
        <input id="s-passwordConfirm" v-model="form.passwordConfirm" type="password" autocomplete="new-password" placeholder="비밀번호 다시 입력" :aria-invalid="!!shown.passwordConfirm" :aria-describedby="describedBy('passwordConfirm')" />
        <span v-if="shown.passwordConfirm" id="s-passwordConfirm-error" class="field-error">{{ shown.passwordConfirm }}</span>
      </div>

      <p class="footnote span">기본 신청 역할은 {{ DEFAULT_SIGNUP_ROLE_LABEL }}입니다. 팀장 권한은 관리자가 부여합니다.</p>

      <div class="span">
        <label class="check">
          <input id="s-agreed" v-model="form.agreed" type="checkbox" :aria-invalid="!!shown.agreed" :aria-describedby="describedBy('agreed')" />
          [필수] 이용약관 및 개인정보 수집·이용에 동의합니다.
        </label>
        <span v-if="shown.agreed" id="s-agreed-error" class="field-error">{{ shown.agreed }}</span>
      </div>

      <button class="btn btn-primary span" type="submit">가입 승인 요청</button>
    </form>

    <div class="bottom">
      <span class="muted">이미 가입하셨나요?</span>
      <RouterLink :to="{ name: 'login' }" class="to-login">로그인</RouterLink>
    </div>
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
