<script setup lang="ts">
import { computed, nextTick, reactive, ref } from 'vue'
import { companyInput, newCompany, numberOrNull, reviewOffer } from '@/api/review'
import { ApiError } from '@/api/client'

const form = reactive({ supplier: '', rfq_id: '', as_of: new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' }),
  unit_price: '', payment_days: '', price_linkage: '', raw_material_ratio: '', energy_ratio: '', contract_days: '', linkage_waived_by_agreement: false })
const companies = reactive([{ role: 'supplier', label: '공급자', data: newCompany() }, { role: 'buyer', label: '구매자', data: newCompany() }])
const numericFields = [{ key: 'average_revenue_eok', label: '평균매출액 (억원)' }, { key: 'assets_eok', label: '자산총액 (억원)' }] as const
const triFields = [{ key: 'independence_met', label: '독립성 요건 충족' }, { key: 'has_related_enterprises', label: '관계기업 있음' }, { key: 'seat_manufacturing', label: '의자·좌석 제조업 해당' }, { key: 'deemed_sme', label: '유예·간주 중소기업 해당' }] as const
const resultPanel = ref<HTMLElement | null>(null)
const busy = ref(false), error = ref(''), submitted = ref('')
const result = ref<Awaited<ReturnType<typeof reviewOffer>> | null>(null)
const payload = computed(() => ({ ...form, supplier: form.supplier.trim(), rfq_id: form.rfq_id.trim(),
  unit_price: Number(form.unit_price), payment_days: Number(form.payment_days), contract_days: Number(form.contract_days),
  price_linkage: form.price_linkage === 'true', raw_material_ratio: numberOrNull(form.raw_material_ratio) === null ? null : Number(form.raw_material_ratio) / 100,
  energy_ratio: numberOrNull(form.energy_ratio) === null ? null : Number(form.energy_ratio) / 100,
  supplier_size: companyInput(companies[0]!.data), buyer_size: companyInput(companies[1]!.data) }))
const stale = computed(() => submitted.value !== JSON.stringify(payload.value))
const sources = computed(() => result.value?.answer.evidence.filter(e => e.source === 'law') ?? [])
const sizes = computed(() => (result.value?.answer.evidence.filter(e => e.kind === 'company_size') ?? []).map(e => ({ ...e, assessment: JSON.parse(e.text) })))
const state = (v: unknown) => v === true ? '해당' : v === false ? '해당하지 않음' : '판정 보류'
const incomplete = computed(() => result.value?.routing.outcomes.some(o => o.status !== 'ok'))
function safeUrl(value: string | null) { return value && /^https?:\/\//i.test(value) ? value : undefined }
async function submit() {
  if (busy.value) return
  busy.value = true; error.value = ''; result.value = null
  try {
    const input = JSON.stringify(payload.value); submitted.value = input
    result.value = await reviewOffer(JSON.parse(input))
    await nextTick(); resultPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  catch (e) { error.value = e instanceof ApiError ? `검토 요청 실패 (${e.code}). 입력값과 서버 상태를 확인한 뒤 다시 시도해 주세요.` : '응답을 확인할 수 없습니다. 다시 시도해 주세요.' }
  finally { busy.value = false }
}
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
      <p>실행 상태: {{ result.execution_status }} · {{ result.mode === 'live' ? '실제 서버 검토' : '테스트 모드' }}</p>
      <p v-if="incomplete" class="error">일부 조회가 완료되지 않았습니다. 아래 안내와 근거를 확인하세요.</p>
      <div class="sizes"><article v-for="s in sizes" :key="s.id"><h3>{{ s.meta.role === 'buyer' ? '구매자' : '공급자' }}</h3><p>중소기업: {{ state(s.assessment.is_sme) }}</p><p>소기업: {{ state(s.assessment.is_small_business) }}</p></article></div>
      <h3>최종 검토 답변</h3><div class="answer">{{ result.answer.text }}</div>
      <details><summary>검토에 사용한 입력값</summary><pre>{{ JSON.stringify(JSON.parse(submitted), null, 2) }}</pre></details>
      <nav aria-label="답변 인용 근거"><a v-for="cite in result.answer.citations" :key="cite.n" :href="`#review-${cite.evidence_id}`" class="citation">[{{ cite.n }}] {{ cite.label }}</a></nav>
      <h3>검색 자료와 판단 근거 (법령 자료 {{ sources.length }}건)</h3>
      <details v-for="e in result.answer.evidence" :key="e.id" :id="`review-${e.id}`"><summary>{{ e.id }} · {{ e.label }}</summary><a v-if="safeUrl(e.source_url)" :href="safeUrl(e.source_url)" target="_blank" rel="noopener noreferrer">공식 원문 열기</a><pre>{{ e.text }}</pre></details>
      <p class="footnote">요청 ID: {{ result.request_id }} · 결과는 이 화면에서 확인할 수 있으며 별도 저장되지 않습니다.</p>
    </section>
  </main>
</template>
<style scoped>
.review{display:flex;flex-direction:column;gap:20px}.fields{border:0;padding:0;margin:18px 0;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.fields label{display:flex;flex-direction:column;gap:8px;font-size:13px}.fields input,.fields select{padding:9px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text);min-width:0}.fields .check{flex-direction:row;align-items:center}.company{border-top:1px solid var(--border);padding-top:20px;margin-top:20px}.btn{margin-top:18px}.sizes{display:flex;gap:30px;margin:20px 0}.sizes article{padding:16px;background:var(--surface-2);border-radius:8px;flex:1}.answer{white-space:pre-wrap;line-height:1.8;margin:16px 0}details{padding:12px 0;border-bottom:1px solid var(--border)}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:400px;overflow:auto}.citation{display:block;font-size:12px;margin:6px 0}.error{color:var(--danger)}h3{margin-top:20px}@media(max-width:800px){.fields{grid-template-columns:1fr}.sizes{flex-direction:column}}
</style>
