<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { companyInput, newCompany, numberOrNull } from '@/api/review'
import TransactionFactsForm from '@/components/TransactionFactsForm.vue'
import NegotiationProposalPanel from '@/components/NegotiationProposalPanel.vue'
import { ApiError } from '@/api/client'
import {
  createNegotiation, createProposal, getNegotiation, getProposal, getReview, runReview,
  type Eligibility, type OfferGoals, type ProposalView, type StoredReview, type Workspace,
} from '@/api/negotiations'

const form = reactive({ supplier: '', rfq_id: '', as_of: new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' }),
  unit_price: '', payment_days: '', price_linkage: '', raw_material_ratio: '', energy_ratio: '', contract_days: '', linkage_waived_by_agreement: false })
const transaction = ref<Record<string, unknown>>({})
const displayNames: Record<string, string> = {
  ready: '입력 사실 기준 검토 완료', needs_information: '추가 확인 필요', blocked: '제안 보류', supported: '입력 사실 확인',
  COMPLETED: '완료', NEEDS_INPUT: '추가 입력 필요', FAILED: '실패', offer: '협상 초안 생성', no_offer: '협상안 생성 보류',
  'transaction.applicability': '적용 전제', 'price.scope': '단가', 'payment.deadline': '지급기한', 'linkage.agreement': '연동 약정', 'duration.commitment': '계약 기간',
  unit_price: '단가', payment_days: '지급기한', price_linkage: '연동 약정', contract_days: '계약 기간',
  transaction_type: '거래 유형', party_requirements_confirmed: '당사자 요건', contracted_on: '최초 계약일', renewed_on: '갱신일', future_contract_date_review: '미래 계약일 확인',
  price_scope: '단가 적용 범위', previous_unit_price: '기존 발주 단가', reduction_reason: '감액 사유', reduction_evidence: '감액 증빙', reduction_justification_review: '감액 정당성 검토',
  payment_basis: '지급 기산점', receipt_date: '목적물 수령일', completion_date: '용역 완료일', monthly_agreed_date: '합의한 계산서 발행일', agreed_due_date: '약정 지급일',
  upstream_payment_applicable: '기성금·준공금 수령에 따른 지급 여부', upstream_received_on: '기성금·준공금 수령일', payment_base_date: '지급 기산일',
  payment_exception_legal_review: '지급기한 예외의 법적 요건 검토', payment_exception_reason: '지급기한 예외 사유', payment_exception_evidence: '지급기한 예외 증빙',
  legacy_contract_linkage_review: '연동제 시행 전 계약의 적용례 검토', energy_linkage_transition_review: '에너지 연동 시행 경과조치 검토',
  actual_contract_amount: '실제 계약금액', committed_quantity: '확정 구매물량', linkage_details: '연동 약정 상세',
  linkage_exception_or_waiver_review: '연동 예외 또는 미연동 합의 검토', linkage_exception_legal_review: '연동 예외의 법적 요건 검토',
  material: '원재료', energy: '에너지', name: '대상 이름', cost_ratio: '원가 비중', indicator: '기준 지표', formula: '조정 산식',
  trigger_ratio: '조정요건', frequency: '조정 주기', reference_period: '기준·비교 시점', reflection_date: '조정 반영일', evidence_references: '증빙 자료',
}
const label = (key: string) => displayNames[key] ?? '추가 검토 항목'
function missingLabel(key: string) {
  if (!key.startsWith('linkage_details.')) return label(key)
  return key.split('.').map(part => /^\d+$/.test(part) ? `${Number(part) + 1}번 대상` : label(part)).join(' · ')
}
const missingLabels = (keys: string[]) => keys.map(missingLabel).join(', ')
const companies = reactive([{ role: 'supplier', label: '공급자', data: newCompany() }, { role: 'buyer', label: '구매자', data: newCompany() }])
const numericFields = [{ key: 'average_revenue_eok', label: '평균매출액 (억원)' }, { key: 'assets_eok', label: '자산총액 (억원)' }] as const
const triFields = [{ key: 'independence_met', label: '독립성 요건 충족' }, { key: 'has_related_enterprises', label: '관계기업 있음' }, { key: 'seat_manufacturing', label: '의자·좌석 제조업 해당' }, { key: 'deemed_sme', label: '유예·간주 중소기업 해당' }] as const
const resultPanel = ref<HTMLElement | null>(null)
const busy = ref(false), error = ref(''), submitted = ref('')
const result = ref<StoredReview | null>(null)
const route = useRoute(), router = useRouter()
// 검토 결과는 협상 작업에 저장된다. 협력사·RFQ가 바뀌면 새 작업을 만든다.
const workspace = ref<Workspace | null>(null)
const proposal = ref<ProposalView | null>(null)
const goals = reactive({ intent: '', target_price: '', max_price: '', notes: '' })
const proposing = ref(false), proposalError = ref(''), restored = ref('')
const ELIGIBILITY: Record<string, string> = { ready: '협상안 생성 가능', conditional: '미확인 사항을 유지한 조건부 생성', blocked: '생성 보류' }
const eligibilityLabel = (v: Eligibility) => v ? ELIGIBILITY[v] : '거래 사실 검토 결과 없음'
const goalsInvalid = computed(() => goals.target_price !== '' && goals.max_price !== '' && Number(goals.max_price) < Number(goals.target_price))
const proposeBlocked = computed(() => {
  if (!result.value || !workspace.value || result.value.review_revision === 0) return '저장된 검토가 없습니다.'
  if (stale.value) return '입력값이 바뀌었습니다. 다시 검토한 뒤 만들 수 있습니다.'
  if (result.value.review_revision !== workspace.value.review_head_revision) return '최신 검토가 아닙니다. 다시 검토하세요.'
  if (result.value.eligibility === 'blocked') return '검토 결과가 생성 보류입니다. 아래 쟁점을 확인하세요.'
  if (result.value.eligibility === null) return '거래 사실 검토 결과가 없어 만들 수 없습니다.'
  if (goalsInvalid.value) return '최대 수용가는 목표 단가 이상이어야 합니다.'
  return ''
})
const payload = computed(() => ({ ...form, supplier: form.supplier.trim(), rfq_id: form.rfq_id.trim(),
  unit_price: Number(form.unit_price), payment_days: Number(form.payment_days), contract_days: Number(form.contract_days),
  price_linkage: form.price_linkage === 'true', raw_material_ratio: numberOrNull(form.raw_material_ratio) === null ? null : Number(form.raw_material_ratio) / 100,
  energy_ratio: numberOrNull(form.energy_ratio) === null ? null : Number(form.energy_ratio) / 100,
  transaction: transaction.value, supplier_size: companyInput(companies[0]!.data), buyer_size: companyInput(companies[1]!.data) }))
const stale = computed(() => submitted.value !== JSON.stringify(payload.value))
const sources = computed(() => result.value?.answer.evidence.filter(e => e.source === 'law') ?? [])
const sizes = computed(() => (result.value?.answer.evidence.filter(e => e.kind === 'company_size') ?? []).map(e => ({ ...e, assessment: JSON.parse(e.text) })))
const state = (v: unknown) => v === true ? '해당' : v === false ? '해당하지 않음' : '판정 보류'
const incomplete = computed(() => result.value?.routing.outcomes.some(o => o.status !== 'ok'))
function safeUrl(value: string | null) { return value && /^https?:\/\//i.test(value) ? value : undefined }
async function submit() {
  if (busy.value) return
  busy.value = true; error.value = ''; result.value = null; restored.value = ''
  try {
    const input = JSON.stringify(payload.value); submitted.value = input
    const offer = JSON.parse(input) as Record<string, unknown>
    const supplier = String(offer.supplier), rfq = String(offer.rfq_id)
    if (!workspace.value || workspace.value.supplier_id !== supplier || workspace.value.rfq_id !== rfq) {
      workspace.value = await createNegotiation(supplier, rfq); proposal.value = null
      await router.replace({ query: { ...route.query, negotiation: workspace.value.negotiation_id } })
    }
    delete offer.supplier; delete offer.rfq_id
    const head = workspace.value.review_head_revision
    result.value = await runReview(workspace.value.negotiation_id, offer, head > 0 ? head : null)
    workspace.value = await getNegotiation(workspace.value.negotiation_id)
    await nextTick(); resultPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  catch (e) {
    if (e instanceof ApiError && e.code === 'REVISION_CONFLICT' && workspace.value) {
      workspace.value = await getNegotiation(workspace.value.negotiation_id).catch(() => workspace.value)
      error.value = '다른 탭에서 검토가 저장되었습니다. 최신 상태로 다시 실행해 주세요.'
    } else if (e instanceof ApiError && e.code === 'SUPPLIER_NOT_FOUND') error.value = '등록되지 않은 공급자 ID입니다.'
    else error.value = e instanceof ApiError ? `검토 요청 실패 (${e.code}). 입력값과 서버 상태를 확인한 뒤 다시 시도해 주세요.` : '응답을 확인할 수 없습니다. 다시 시도해 주세요.'
  }
  finally { busy.value = false }
}
async function propose() {
  if (proposing.value || proposeBlocked.value || !workspace.value || !result.value) return
  proposing.value = true; proposalError.value = ''
  try {
    const offer: OfferGoals = {}
    if (goals.intent) offer.intent = goals.intent as OfferGoals['intent']
    if (goals.target_price !== '') offer.target_price = Number(goals.target_price)
    if (goals.max_price !== '') offer.max_price = Number(goals.max_price)
    if (goals.notes.trim()) offer.notes = goals.notes.trim()
    proposal.value = null
    proposal.value = await createProposal(workspace.value.negotiation_id, result.value.review_revision, offer)
    workspace.value = await getNegotiation(workspace.value.negotiation_id)
  } catch (e) {
    const code = e instanceof ApiError ? e.code : ''
    proposalError.value = code === 'REVIEW_RECHECK_REQUIRED' ? '검토가 바뀌었습니다. 다시 검토하세요.'
      : code === 'REVIEW_NOT_READY' ? '검토 결과로는 협상안을 만들 수 없습니다(생성 보류 또는 검토 결과 없음).'
      : code ? `협상안 생성 실패 (${code}).` : '응답을 확인할 수 없습니다. 다시 시도해 주세요.'
  } finally { proposing.value = false }
}
// 주소의 ?negotiation= 으로 저장된 검토와 협상안을 다시 연다. 폼 입력은 검토에 쓴 값으로 채운다.
onMounted(async () => {
  const id = typeof route.query.negotiation === 'string' ? route.query.negotiation : ''
  if (!id) return
  try {
    workspace.value = await getNegotiation(id)
    form.supplier = workspace.value.supplier_id; form.rfq_id = workspace.value.rfq_id
    if (workspace.value.review_head_revision > 0) {
      result.value = await getReview(id, workspace.value.review_head_revision)
      const s = result.value.input_snapshot as Record<string, any>
      Object.assign(form, { as_of: s.as_of ?? form.as_of, unit_price: String(s.unit_price ?? ''), payment_days: String(s.payment_days ?? ''),
        contract_days: String(s.contract_days ?? ''), price_linkage: String(s.price_linkage ?? ''), linkage_waived_by_agreement: s.linkage_waived_by_agreement === true,
        raw_material_ratio: typeof s.raw_material_ratio === 'number' ? String(s.raw_material_ratio * 100) : '',
        energy_ratio: typeof s.energy_ratio === 'number' ? String(s.energy_ratio * 100) : '' })
      submitted.value = JSON.stringify(payload.value)
      restored.value = '저장된 검토를 불러왔습니다. 거래 사실과 기업 정보 입력칸은 비어 있으니, 다시 검토하려면 다시 입력하세요.'
    }
    if (workspace.value.current_proposal_id) proposal.value = await getProposal(id, workspace.value.current_proposal_id)
  } catch { error.value = '저장된 협상 작업을 불러오지 못했습니다.' }
})
</script>

<template>
  <header class="page-header"><h1>법규 검토</h1><span class="context">기업 정보와 계약 조건에 따른 검토</span></header>
  <main class="page-body review">
    <form @submit.prevent="submit" class="panel panel-pad">
      <h2>계약 조건</h2>
      <p class="sub">등록된 협력사·RFQ를 입력하세요. 기업 정보는 확인한 자료를 기준으로 입력하며, 빈 값과 ‘모름’은 판정 보류로 전달됩니다.</p>
      <fieldset :disabled="busy" class="fields">
        <label>공급자 ID *<input v-model="form.supplier" required pattern="SUP-[0-9]{4}" placeholder="SUP-0001" /></label>
        <label>RFQ ID *<input v-model="form.rfq_id" required pattern="RFQ-[0-9]{4}-[0-9]{3}" placeholder="RFQ-2026-001" /></label>
        <label>검토 기준일 *<input v-model="form.as_of" type="date" required /></label>
        <label>제안 단가 (원) *<input v-model="form.unit_price" type="number" min="1" step="1" required /></label>
        <label>지급기한 (일) *<input v-model="form.payment_days" type="number" min="1" step="1" required /></label>
        <label>계약 기간 (일) *<input v-model="form.contract_days" type="number" min="1" step="1" required /></label>
        <label>연동 약정 포함 *<select v-model="form.price_linkage" required><option value="">선택</option><option value="true">예</option><option value="false">아니오</option></select></label>
        <label>원재료 비중 (%)<input v-model="form.raw_material_ratio" type="number" min="0" max="100" step="any" /></label>
        <label>에너지 비용 비중 (%)<input v-model="form.energy_ratio" type="number" min="0" max="100" step="any" /></label>
        <label class="check"><input type="checkbox" v-model="form.linkage_waived_by_agreement" />미연동 합의 있음</label>
      </fieldset>
      <TransactionFactsForm :disabled="busy" @change="transaction = $event" />
      <section v-for="c in companies" :key="c.role" class="company">
        <h2>{{ c.label }} 기업 정보</h2>
        <fieldset :disabled="busy" class="fields">
          <label :for="c.role + '-ksic'">{{ c.label }} KSIC 업종 코드<input :id="c.role + '-ksic'" v-model="c.data.ksic_code" placeholder="예: C30399" maxlength="20" /></label>
          <label v-for="f in numericFields" :key="f.key">{{ c.label }} {{ f.label }}<input v-model="c.data[f.key]" type="number" min="0" step="any" /></label>
          <label v-for="f in triFields" :key="f.key">{{ c.label }} {{ f.label }}<select v-model="c.data[f.key]"><option value="">모름</option><option value="true">예</option><option value="false">아니오</option></select></label>
          <template v-if="c.data.has_related_enterprises === 'true'">
            <label>{{ c.label }} 관계기업 합산 평균매출액 (억원)<input v-model="c.data.related_aggregate_revenue_eok" type="number" min="0" step="any" /></label>
            <label class="check"><input v-model="c.data.related_calculation_verified" type="checkbox" />{{ c.label }} 관계기업 합산 계산 검증 완료</label>
          </template>
          <label>{{ c.label }} 확인 자료·증빙 번호<input v-model="c.data.evidence_reference" maxlength="500" /></label>
          <label class="check"><input v-model="c.data.transition_reviewed" type="checkbox" />{{ c.label }} 유예·경과조치 검토 완료</label>
        </fieldset>
      </section>
      <p class="footnote">현재 자동 규모 판단은 지원되는 제조업과 기준일 범위에서 수행됩니다. 지원 범위 밖이거나 근거가 부족하면 판정을 보류합니다.</p>
      <button type="submit" class="btn btn-primary" :disabled="busy">{{ busy ? '자료 검색 및 검토 중…' : '법규 검토 실행' }}</button>
    </form>
    <p v-if="busy" role="status">공식 자료 검색 → 기업 규모 판단 → 검토 결과 작성 중입니다.</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <section v-if="result" ref="resultPanel" class="panel panel-pad" aria-label="검토 결과">
      <h2>검토 결과</h2>
      <p v-if="stale" role="alert" class="error">입력값이 변경되었습니다. 아래는 이전 입력의 결과입니다. 다시 검토해 주세요.</p>
      <p v-if="restored" class="footnote">{{ restored }}</p>
      <p>실행 상태: {{ label(result.execution_status) }} · {{ result.mode === 'live' ? '실제 서버 검토' : '테스트 모드' }}</p>
      <p v-if="result.review_revision > 0">검토 r{{ result.review_revision }} · {{ eligibilityLabel(result.eligibility) }}</p>
      <p v-if="incomplete" class="error">일부 조회가 완료되지 않았습니다. 아래 안내와 근거를 확인하세요.</p>
      <div class="sizes"><article v-for="s in sizes" :key="s.id"><h3>{{ s.meta.role === 'buyer' ? '구매자' : '공급자' }}</h3><p>중소기업: {{ state(s.assessment.is_sme) }}</p><p>소기업: {{ state(s.assessment.is_small_business) }}</p></article></div>
      <section v-if="result.legal_review" class="company" aria-label="거래 사실 기반 검토">
        <h3>거래 사실과 조건별 판단 근거</h3>
        <p>검토 상태: {{ label(result.legal_review.assessment.status) }}</p>
        <p v-if="result.legal_review.assessment.missing_fields.length">추가 확인: {{ missingLabels(result.legal_review.assessment.missing_fields) }}</p>
        <details><summary>쟁점별 법규 판단 펼치기</summary>
        <article v-for="finding in result.legal_review.assessment.findings" :key="finding.id">
          <h4>{{ label(finding.id) }} · {{ label(finding.status) }}</h4><p>{{ finding.summary }}</p>
          <p v-if="finding.missing_fields.length">추가 확인: {{ missingLabels(finding.missing_fields) }}</p>
          <a v-for="(law, index) in finding.law_refs" :key="index" :href="safeUrl(law.source_url || law.url)" target="_blank" rel="noopener noreferrer">{{ law.title }} </a>
        </article>
        </details>
        <p class="review-note">사용자가 제공한 거래 사실을 전제로 한 검토입니다. 증빙의 진위와 사실관계를 서비스가 직접 확인한 것은 아니며, 검토 완료가 적법성 보장을 의미하지 않습니다.</p>
        <details><summary>검토 식별 정보</summary><p>{{ result.legal_review.review_id }} · {{ result.legal_review.version }} · 작업 {{ result.review_id }}</p></details>
        <p class="footnote">조건이 바뀌면 거래 사실과 변경 조건을 함께 다시 검토해야 합니다. 검토 결과는 협상 조건의 판단 근거로 전달됩니다.</p>

      </section>
      <details><summary>전체 법규 검토 답변과 공식 자료 ({{ sources.length }}건)</summary>
      <h3>최종 검토 답변</h3><div class="answer">{{ result.answer.text }}</div>
      <details><summary>검토에 사용한 입력값</summary><pre>{{ JSON.stringify(result.review_revision > 0 ? result.input_snapshot : JSON.parse(submitted), null, 2) }}</pre></details>
      <nav aria-label="답변 인용 근거"><a v-for="cite in result.answer.citations" :key="cite.n" :href="`#review-${cite.evidence_id}`" class="citation">[{{ cite.n }}] {{ cite.label }}</a></nav>
      <h3>검색 자료와 판단 근거 (법령 자료 {{ sources.length }}건)</h3>
      <details v-for="e in result.answer.evidence" :key="e.id" :id="`review-${e.id}`"><summary>{{ e.id }} · {{ e.label }}</summary><a v-if="safeUrl(e.source_url)" :href="safeUrl(e.source_url)" target="_blank" rel="noopener noreferrer">공식 원문 열기</a><pre>{{ e.text }}</pre></details>
      </details>
      <p class="footnote">요청 ID: {{ result.request_id }} · {{ result.review_revision > 0 ? `검토 결과는 저장되며 r${result.review_revision}으로 협상안을 만듭니다.` : '실행에 실패한 검토는 저장되지 않습니다.' }}</p>
    </section>
    <section v-if="result && result.review_revision > 0" class="panel panel-pad" aria-label="협상안 만들기">
      <h2>협상안 만들기</h2>
      <p class="sub">검토 r{{ result.review_revision }}의 거래 사실과 기업 정보를 이어받습니다. 목표 단가를 비우면 RFQ 목표 단가를 씁니다. 검토한 제안 단가는 자동으로 쓰지 않습니다.</p>
      <fieldset :disabled="proposing" class="fields">
        <label>협상 방향<select v-model="goals.intent"><option value="">자동</option><option value="target">목표가 중심</option><option value="market">시장가 중심</option><option value="payment">지급 조건 양보</option></select></label>
        <label>목표 단가 (원)<input v-model="goals.target_price" type="number" min="1" step="1" /></label>
        <label>최대 수용가 (원)<input v-model="goals.max_price" type="number" min="1" step="1" /></label>
        <label class="wide-field">납품·비고<input v-model="goals.notes" maxlength="500" /></label>
      </fieldset>
      <p v-if="result.eligibility === 'conditional' && !proposeBlocked" class="footnote">미확인 사항이 남은 조건부 초안으로 만듭니다.</p>
      <p v-if="proposeBlocked" class="error">{{ proposeBlocked }}</p>
      <button type="button" class="btn btn-primary" :disabled="!!proposeBlocked || proposing" @click="propose">{{ proposing ? '협상안 만드는 중…' : (proposal ? '협상안 다시 만들기' : '협상안 만들기') }}</button>
      <p v-if="proposing" role="status">기준값 조회 → 후보 계산·법규 검사 → 문서 작성 중입니다.</p>
      <p v-if="proposalError" role="alert" class="error">{{ proposalError }}</p>
    </section>
    <NegotiationProposalPanel v-if="proposal && workspace" :key="proposal.proposal_id" :negotiation-id="workspace.negotiation_id" :initial="proposal" />
  </main>
</template>
<style scoped>
.review-note,.stale-warning{padding:14px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);line-height:1.7}.stale-warning{border-color:var(--danger)}.review{display:flex;flex-direction:column;gap:20px}.fields{border:0;padding:0;margin:18px 0;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.fields label{display:flex;flex-direction:column;gap:8px;font-size:13px}.fields input,.fields select{padding:9px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text);min-width:0}.fields .check{flex-direction:row;align-items:center}.company{border-top:1px solid var(--border);padding-top:20px;margin-top:20px}.btn{margin-top:18px}.sizes{display:flex;gap:30px;margin:20px 0}.sizes article{padding:16px;background:var(--surface-2);border-radius:8px;flex:1}.answer{white-space:pre-wrap;line-height:1.8;margin:16px 0}details{padding:12px 0;border-bottom:1px solid var(--border)}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:400px;overflow:auto}.citation{display:block;font-size:12px;margin:6px 0}.error{color:var(--danger)}h3{margin-top:20px}.wide-field{grid-column:1/-1}@media(max-width:800px){.fields{grid-template-columns:1fr}.sizes{flex-direction:column}}
</style>
