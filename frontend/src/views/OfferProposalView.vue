<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ApiError } from '@/api/client'
import {
  createOfferProposal, describeOfferError, previewOfferMail, renderOfferDocument, reviewOfferExcel, saveAttachment, sendOfferMail,
  type FileFormat, type MailPreview, type MailSendResult, type OfferProposal, type OfferReview, type RenderedOffer,
} from '@/api/offer-proposals'
import MailPreviewDialog from '@/components/offer/MailPreviewDialog.vue'
import OfferDocumentEditor from '@/components/offer/OfferDocumentEditor.vue'
import OfferReviewTable from '@/components/offer/OfferReviewTable.vue'
import { session } from '@/stores/session'

// 단계는 ① 엑셀 → ② 법규 검토 → ③ 협상안 확인·수정 → ④ 저장·발송 순서로만 열린다.
// 앞 단계 결과가 없거나 바뀌면 뒤 단계 결과를 지우고 화면에서도 내린다.
const MOCK = import.meta.env.VITE_OFFER_API === 'mock'

const supplierName = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const review = ref<OfferReview | null>(null)
const proposal = ref<OfferProposal | null>(null)
const rendered = ref<RenderedOffer | null>(null)
const sent = ref<MailSendResult | null>(null)
const busy = ref<'' | 'review' | 'create' | 'render'>('')
const errors = ref({ review: '', create: '', render: '', save: '' })

const approved = computed(() => review.value?.rows.filter(r => r.verdict === 'approved').length ?? 0)
const rejected = computed(() => review.value?.rows.filter(r => r.verdict === 'rejected').length ?? 0)
const allApproved = computed(() => !!review.value && review.value.rows.length > 0 && rejected.value === 0)

const steps = computed(() => {
  const done = [!!review.value, !!proposal.value, !!rendered.value, !!sent.value]
  const labels = ['1. 엑셀 불러오기', '2. 법규 검토', '3. 협상안 확인·수정', '4. 문서 저장·발송']
  const current = done.indexOf(false)
  return labels.map((label, i) => ({ label, state: done[i] ? 'done' : i === current ? 'current' : 'todo' }))
})

function message(error: unknown, fallback: string) {
  if (!(error instanceof ApiError)) return fallback
  if (error.code === 'OFFER_VIOLATION') return '고친 조건에 위반 소지가 있어 확정하지 못했습니다. 지급기한 등 조건을 다시 확인해 주세요.'
  if (error.code === 'NOT_IMPLEMENTED') return '서버 API가 아직 연결되지 않았습니다.'
  const known = describeOfferError(error)
  if (known) return known
  return `${fallback} (${error.code})`
}

function resetFrom(step: 1 | 2 | 3 | 4) {
  if (step <= 1) review.value = null
  if (step <= 2) proposal.value = null
  if (step <= 3) rendered.value = null
  sent.value = null
  closeMail()
}

// ① 협력사 이름이 바뀌면 이전 검토 결과는 쓰지 않는다
watch(supplierName, name => { if (review.value && name.trim() !== review.value.supplier_name) resetFrom(1) })

function pickFile() { if (supplierName.value.trim()) fileInput.value?.click() }
async function onFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || busy.value) return
  resetFrom(1)
  busy.value = 'review'; errors.value.review = ''
  try { review.value = await reviewOfferExcel(supplierName.value.trim(), file) }
  catch (e) { errors.value.review = message(e, '엑셀을 검토하지 못했습니다. 파일 형식을 확인해 주세요.') }
  finally { busy.value = '' }
}

// ② 모든 항목이 승인일 때만 협상안을 만든다
const step3 = ref<HTMLElement | null>(null)
async function createProposal() {
  if (!review.value || !allApproved.value || busy.value) return
  resetFrom(2)
  busy.value = 'create'; errors.value.create = ''
  try {
    proposal.value = await createOfferProposal(review.value, session.value)
    await nextTick(); step3.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  catch (e) { errors.value.create = message(e, '협상안을 만들지 못했습니다.') }
  finally { busy.value = '' }
}

// ③ 고친 제안서를 다시 검사해 확정본을 받는다. 다시 고치면 버전을 올리고 ④를 내린다
async function confirmDocument() {
  if (!proposal.value || busy.value) return
  busy.value = 'render'; errors.value.render = ''
  try {
    const result = await renderOfferDocument(proposal.value.document, 'both', proposal.value.briefing)
    rendered.value = result
    proposal.value.document = { ...result.document }
    proposal.value.check = result.check
  }
  catch (e) { errors.value.render = message(e, '제안서를 확정하지 못했습니다.') }
  finally { busy.value = '' }
}
function editAgain() {
  if (!proposal.value) return
  resetFrom(3)
  proposal.value.document = { ...proposal.value.document, status: 'draft', version: proposal.value.document.version + 1, content_hash: null }
}

// ④ 저장과 메일 발송은 확정본으로만 한다
function save(format: FileFormat) {
  errors.value.save = ''
  try { if (rendered.value) saveAttachment(rendered.value, format) }
  catch (e) { errors.value.save = message(e, '파일을 저장하지 못했습니다.') }
}

const mailOpen = ref(false)
const mailPreview = ref<MailPreview | null>(null)
const mailLoading = ref(false), mailSending = ref(false), mailError = ref('')
function closeMail() { mailOpen.value = false; mailPreview.value = null; mailError.value = '' }
async function openMail() {
  if (!rendered.value) return
  mailOpen.value = true; mailLoading.value = true; mailError.value = ''; mailPreview.value = null
  try { mailPreview.value = await previewOfferMail(rendered.value) }
  catch (e) { mailError.value = message(e, '메일 미리보기를 만들지 못했습니다.') }
  finally { mailLoading.value = false }
}
async function sendMail(note: string) {
  if (!rendered.value || !mailPreview.value || mailSending.value) return
  mailSending.value = true; mailError.value = ''
  try {
    // 추가 문구를 썼으면 그 문구로 미리보기(확인 토큰)를 다시 받고 보낸다
    const extra = note.trim() || undefined
    const preview = extra === undefined ? mailPreview.value : await previewOfferMail(rendered.value, extra)
    sent.value = await sendOfferMail(rendered.value, preview, extra)
    mailOpen.value = false; mailPreview.value = null
  }
  catch (e) { mailError.value = message(e, '메일을 보내지 못했습니다. 미리보기를 다시 열어 주세요.') }
  finally { mailSending.value = false }
}
</script>

<template>
  <header class="page-header">
    <h1>협상안 검토</h1>
    <span class="context">엑셀 불러오기 → 법규 검토(승인/반려) → 협상안 만들기 → 제안서·내부 브리핑 → Word·PDF·메일</span>
    <span class="right"><span v-if="MOCK" class="badge badge-gray">예시 데이터 · 서버 연결 전</span></span>
  </header>

  <main class="page-body offer">
    <ol class="steps" aria-label="진행 단계">
      <template v-for="(s, i) in steps" :key="s.label">
        <li class="step" :class="s.state" :aria-current="s.state === 'current' ? 'step' : undefined">{{ s.state === 'done' ? '✓ ' : '' }}{{ s.label }}</li>
        <li v-if="i < steps.length - 1" class="sep" aria-hidden="true">›</li>
      </template>
    </ol>

    <!-- ① 협상 조건 불러오기 -->
    <section class="panel panel-pad" aria-labelledby="step1-title">
      <div class="panel-title"><div>
        <h2 id="step1-title">① 협상 조건 불러오기</h2>
        <p class="sub">협력사 이름을 입력하고, 협상 조건이 담긴 엑셀 파일을 불러옵니다.</p>
      </div></div>
      <div class="input-row">
        <div class="field name">
          <label for="supplier-name">협력사 회사명</label>
          <input id="supplier-name" v-model="supplierName" type="text" placeholder="회사 이름을 입력하세요" maxlength="100" :disabled="busy === 'review'" />
        </div>
        <button type="button" class="btn" :disabled="!supplierName.trim() || busy === 'review'" @click="pickFile">
          {{ busy === 'review' ? '법규 검토 중… (1~2분)' : '⇪ 엑셀 파일 불러오기' }}
        </button>
        <input ref="fileInput" type="file" accept=".xlsx" class="sr-only" tabindex="-1" @change="onFile" />
        <span v-if="review" class="muted file">✓ {{ review.file_name }}</span>
      </div>
      <p v-if="!supplierName.trim()" class="muted hint">회사명을 먼저 입력하면 엑셀을 불러올 수 있어요. 엑셀에 여러 협력사가 있으면 이 회사의 행만 검토 결과로 보여 줘요.</p>
      <p v-if="errors.review" role="alert" class="field-error">{{ errors.review }}</p>
    </section>

    <!-- ② 법규 검토 결과: ①이 끝나야 보인다 -->
    <section v-if="review" class="panel panel-pad" aria-labelledby="step2-title">
      <div class="panel-title">
        <div>
          <h2 id="step2-title">② 법규 검토 결과</h2>
          <p class="sub">check_offer v2 (rules.py) · 항목마다 승인 또는 반려로 판정합니다.</p>
        </div>
        <p class="counts"><span class="ok">승인 {{ approved }}</span><span class="ng">반려 {{ rejected }}</span></p>
      </div>
      <OfferReviewTable :rows="review.rows" />
      <div class="create-row">
        <div>
          <p v-if="allApproved" class="ok strong">✓ 모든 항목이 승인되었습니다. 협상안을 만들 수 있어요.</p>
          <p v-else class="ng strong">반려 {{ rejected }}건이 있어 협상안을 만들 수 없어요. 조건을 고친 엑셀을 다시 불러와 주세요.</p>
          <p class="muted">반려 항목이 하나라도 있으면 [협상안 만들기] 버튼이 비활성화됩니다.</p>
        </div>
        <button type="button" class="btn btn-primary wide" :disabled="!allApproved || busy === 'create'" @click="createProposal">
          {{ busy === 'create' ? '협상안 만드는 중…' : proposal ? '협상안 다시 만들기' : '협상안 만들기' }}
        </button>
      </div>
      <p v-if="errors.create" role="alert" class="field-error">{{ errors.create }}</p>
      <p class="footnote">* 판정은 법률 판단의 확정이 아닙니다. 적용 조건과 시행일은 담당자가 반드시 확인해야 합니다.</p>
    </section>

    <!-- ③ 협상안 보기·수정: ②에서 협상안을 만들어야 보인다 -->
    <section v-if="proposal" ref="step3" class="panel panel-pad" aria-labelledby="step3-title">
      <div class="panel-title">
        <div>
          <h2 id="step3-title">③ 협상안 확인·수정</h2>
          <p class="sub">만들어진 협력사 발송용 제안서와 내부 협상 브리핑(대외비)을 확인하고 고칠 수 있습니다.</p>
        </div>
        <span class="badge" :class="rendered ? '' : 'badge-gray'">{{ rendered ? `확정 · v${proposal.document.version}` : `작성 중 · v${proposal.document.version}` }}</span>
      </div>
      <OfferDocumentEditor v-model:document="proposal.document" v-model:briefing="proposal.briefing" :locked="!!rendered || busy === 'render'" />
      <div class="confirm-row">
        <p class="muted">{{ rendered ? '확정한 제안서로 저장·발송합니다. 고치려면 [다시 수정]을 누르세요.' : '고친 조건은 확정할 때 법규 검사를 다시 거칩니다.' }}</p>
        <button v-if="rendered" type="button" class="btn" @click="editAgain">다시 수정</button>
        <button v-else type="button" class="btn btn-primary wide" :disabled="busy === 'render'" @click="confirmDocument">{{ busy === 'render' ? '검사하는 중…' : '제안서 확정' }}</button>
      </div>
      <p v-if="errors.render" role="alert" class="field-error">{{ errors.render }}</p>
    </section>

    <!-- ④ 저장·발송: ③에서 확정해야 보인다 -->
    <section v-if="proposal && rendered" class="panel panel-pad save-row" aria-labelledby="step4-title">
      <div class="save-text">
        <h2 id="step4-title">④ 문서 저장 · 발송</h2>
        <p class="sub">발신 정보는 로그인 회원(이름·부서·직함·이메일)으로 자동 입력됩니다. Word·PDF에는 발송용 제안서만 담기고, 메일에는 PDF만 첨부합니다. 내부 브리핑은 메일로 보내지 않습니다.</p>
        <p v-if="sent" class="ok strong" role="status">✓ {{ sent.to }}에게 {{ sent.status === 'DRY_RUN' ? '발송 시험 완료(실제 메일은 나가지 않음)' : '발송 완료' }}</p>
        <p v-if="errors.save" role="alert" class="field-error">{{ errors.save }}</p>
      </div>
      <div class="save-actions">
        <button type="button" class="btn" @click="save('docx')">⤓ Word 저장</button>
        <button type="button" class="btn" @click="save('pdf')">⤓ PDF 저장</button>
        <button type="button" class="btn btn-primary wide" @click="openMail">메일 미리보기 후 발송</button>
      </div>
    </section>
  </main>

  <MailPreviewDialog v-if="mailOpen" :preview="mailPreview" :loading="mailLoading" :sending="mailSending" :error="mailError"
    :warnings="rendered?.check.warnings ?? []" @close="closeMail" @send="sendMail" />
</template>

<style scoped>
.steps{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0;padding:0;list-style:none;font-size:11px}
.step{padding:3px 10px;border-radius:10px;color:var(--text-3)}.step.done{background:#e8f4ea;color:var(--success)}
.step.current{background:var(--primary-soft);color:var(--primary);font-weight:600}.sep{color:var(--text-3)}
.input-row{display:flex;flex-wrap:wrap;align-items:flex-end;gap:16px}.name{width:min(360px,100%)}.file{align-self:center}
.hint{margin:8px 0 0}.counts{margin:0;display:flex;gap:10px;font-size:12px}
.ok{color:var(--success)}.ng{color:var(--danger)}.strong{margin:0 0 4px;font-weight:600;font-size:13px}
.create-row,.confirm-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;margin:18px 0 10px}
.confirm-row p{margin:0}.wide{min-width:170px}
.save-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:16px}
.save-text{flex:1 1 420px}.save-text h2{font-size:16px}.save-text .sub{margin:4px 0 0;font-size:12px;color:var(--text-3)}
.save-actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px}.save-actions .btn:not(.wide){min-width:120px}
@media(max-width:800px){.save-actions,.save-actions .btn{width:100%}}
</style>
