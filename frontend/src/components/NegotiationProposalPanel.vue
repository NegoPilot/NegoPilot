<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ApiError } from '@/api/client'
import {
  buildChanges, createExport, downloadExportFile, editStateFrom, getExternalProposal, getProposal, LINKAGE_KEYS, patchProposal, runValidation,
  type EditState, type ExportBundle, type ExternalView, type ProposalView, type ValidationState,
} from '@/api/negotiations'

const props = defineProps<{ negotiationId: string; initial: ProposalView }>()
const view = ref<ProposalView>(props.initial)
const external = ref<ExternalView | null>(null)
const original = ref<EditState>(editStateFrom(props.initial, null))
const edit = ref<EditState>(structuredClone(original.value))
const tab = ref<'internal' | 'external'>('internal')
const busy = ref(''), error = ref(''), notice = ref(''), changeNote = ref('')
const conflictRevision = ref<number | null>(null)
const exports = ref<Record<string, ExportBundle>>({})

const offer = computed(() => (view.value.proposal.offer ?? null) as Record<string, any> | null)
const proposal = computed(() => view.value.proposal as Record<string, any>)
const hasOffer = computed(() => offer.value !== null && proposal.value.status === 'offer')
const dirty = computed(() => Object.keys(buildChanges(original.value, edit.value)).length > 0)
const won = (v: unknown) => typeof v === 'number' ? `${v.toLocaleString('ko-KR')}원` : '-'
const pct = (v: unknown) => typeof v === 'number' ? `${v > 0 ? '+' : ''}${v.toFixed(1)}%` : '-'

const STATUS: Record<string, string> = { not_checked: '검사 전', recheck_required: '수정됨 · 재검사 필요', checking: '검사 중', checked: '검사 완료', failed: '검사 실패' }
const CONCLUSION: Record<string, string> = { no_risk_identified_in_scope: '검사 범위 내 위험 미발견', conditional: '미확인 사항 있음', risk_identified: '위험 발견' }
function badge(v: Pick<ValidationState, 'status' | 'checked_revision' | 'conclusion'>) {
  const head = v.status === 'checked' ? `${STATUS.checked}(r${v.checked_revision})` : STATUS[v.status] ?? v.status
  return v.status === 'checked' && v.conclusion ? `${head} · ${CONCLUSION[v.conclusion] ?? v.conclusion}` : head
}
function message(e: unknown, fallback: string) {
  if (!(e instanceof ApiError)) return fallback
  switch (e.code) {
    case 'REVISION_CONFLICT':
      conflictRevision.value = Number(e.details?.current_revision ?? 0) || null
      return `다른 곳에서 먼저 저장했습니다${conflictRevision.value ? `(현재 r${conflictRevision.value})` : ''}. 최신 버전을 불러온 뒤 다시 수정하세요.`
    case 'REVIEW_RECHECK_REQUIRED': return '거래 사실이 바뀌는 수정입니다. 법규 검토를 다시 실행하고 새 협상안을 만드세요.'
    case 'FIELD_NOT_EDITABLE': return '시스템이 계산한 값이나 근거는 직접 수정할 수 없습니다.'
    case 'PROPOSAL_SUPERSEDED': return '새 협상안이 만들어져 이 협상안은 더 이상 수정할 수 없습니다.'
    case 'VALIDATION_IN_PROGRESS': return '검사가 진행 중입니다. 잠시 후 다시 확인하세요.'
    case 'OFFER_VIOLATION': {
      const findings = Array.isArray(e.details?.findings) ? (e.details!.findings as Array<{ message?: string; code?: string }>) : []
      return `위반 소지가 있어 파일을 만들지 않았습니다: ${findings.map(f => f.message ?? f.code).join(' / ') || e.code}`
    }
    case 'VALIDATION_ERROR': return '입력값을 확인하세요(가격 순서: 제시가 ≤ 목표 합의가 ≤ 최대 수용가, 글자 수 제한).'
    default: return `${fallback} (${e.code})`
  }
}
async function loadExternal() {
  external.value = hasOffer.value ? await getExternalProposal(props.negotiationId, view.value.proposal_id) : null
}
function reset(next: ProposalView) {
  view.value = next
  original.value = editStateFrom(next, external.value)
  edit.value = structuredClone(original.value)
}
async function reload() {
  busy.value = 'reload'; error.value = ''; conflictRevision.value = null
  try {
    const next = await getProposal(props.negotiationId, view.value.proposal_id)
    view.value = next; await loadExternal(); reset(next)
  } catch (e) { error.value = message(e, '협상안을 불러오지 못했습니다') } finally { busy.value = '' }
}
onMounted(async () => { try { await loadExternal(); reset(view.value) } catch (e) { error.value = message(e, '외부 제안서를 불러오지 못했습니다') } })

async function save() {
  const changes = buildChanges(original.value, edit.value)
  if (!Object.keys(changes).length || busy.value) return
  busy.value = 'save'; error.value = ''; notice.value = ''
  try {
    const next = await patchProposal(props.negotiationId, view.value.proposal_id, view.value.head_revision, changes, changeNote.value)
    view.value = next; await loadExternal(); reset(next); changeNote.value = ''
    const parts = [`r${next.head_revision}로 저장했습니다.`]
    if (next.impacts?.external?.recheck?.length) parts.push('외부 제안서에도 반영됩니다.')
    if (next.validation.status === 'recheck_required') parts.push('재검사가 필요합니다.')
    notice.value = parts.join(' ')
  } catch (e) { error.value = message(e, '저장하지 못했습니다') } finally { busy.value = '' }
}
async function recheck() {
  if (dirty.value && !confirm('저장하지 않은 수정이 있습니다. 저장된 현재 버전으로 검사할까요?')) return
  busy.value = 'check'; error.value = ''; notice.value = ''
  try {
    const result = await runValidation(props.negotiationId, view.value.proposal_id, view.value.head_revision)
    view.value = { ...view.value, validation: result.validation }
    notice.value = `r${view.value.head_revision} 검사: ${badge(result.validation)}`
  } catch (e) { error.value = message(e, '재검사하지 못했습니다') } finally { busy.value = '' }
}
async function download(format: string) {
  if (dirty.value && !confirm('저장하지 않은 수정은 파일에 들어가지 않습니다. 저장된 현재 버전으로 받을까요?')) return
  busy.value = `export-${format}`; error.value = ''
  try {
    const bundle = await createExport(props.negotiationId, view.value.proposal_id, view.value.head_revision, [format])
    exports.value = { ...exports.value, [format]: bundle }
    const file = bundle.files.find(f => f.format === format)
    if (file) await downloadExportFile(file)
  } catch (e) { error.value = message(e, '파일을 만들지 못했습니다') } finally { busy.value = '' }
}
const historyLabel = (v: unknown) => v === null || v === undefined || v === '' ? '(비움)' : typeof v === 'object' ? JSON.stringify(v) : String(v)
</script>

<template>
  <section class="panel panel-pad proposal" aria-label="협상안">
    <header class="head">
      <h2>협상안 r{{ view.head_revision }}</h2>
      <span class="badge" :class="view.validation.status">{{ badge(view.validation) }}</span>
    </header>
    <p v-if="view.status !== 'ACTIVE'" class="error">새 협상안이 만들어져 이 협상안은 보관 상태입니다.</p>
    <p v-if="view.review_recheck_required" role="alert" class="error">기반 검토 이후 입력이 바뀌었습니다. 다시 검토하고 새 협상안을 만드세요.</p>
    <p v-if="view.validation.inherited_from" class="note">r{{ view.validation.inherited_from }} 검사 결과를 이어받았습니다 · 수정한 내부 문구는 검사하지 않았습니다.</p>
    <p v-if="view.derived_stale" class="note">계약금액·대비 비율은 재검사 후 갱신됩니다.</p>
    <details v-if="view.validation.scope.length || view.validation.limitations.length" class="scope">
      <summary>검사 범위와 한계</summary>
      <ul><li v-for="s in view.validation.scope" :key="s">검사함: {{ s }}</li><li v-for="l in view.validation.limitations" :key="l">한계: {{ l }}</li></ul>
      <ul v-if="view.validation.violations.length" class="error"><li v-for="(v, i) in view.validation.violations" :key="i">위반 소지: {{ v.message ?? v.code }}</li></ul>
      <ul v-if="view.validation.warnings.length"><li v-for="(w, i) in view.validation.warnings" :key="i">확인 필요: {{ w.message ?? w.code }}</li></ul>
    </details>

    <template v-if="!hasOffer">
      <h3>협상안 생성 보류</h3>
      <ul><li v-for="(r, i) in (proposal.no_offer_reasons ?? [])" :key="i">{{ r }}</li></ul>
    </template>
    <template v-else>
      <div role="tablist" class="tabs">
        <button role="tab" type="button" :aria-selected="tab === 'internal'" :class="{ on: tab === 'internal' }" @click="tab = 'internal'">내부 전략서 <small>대외비</small></button>
        <button role="tab" type="button" :aria-selected="tab === 'external'" :class="{ on: tab === 'external' }" @click="tab = 'external'">외부 제안서 <small>협력사 발송용</small></button>
      </div>

      <fieldset v-show="tab === 'internal'" role="tabpanel" :disabled="!!busy || view.status !== 'ACTIVE'" class="tabpanel">
        <h3>제시 조건 <small>공통 조건 — 외부 제안서에도 반영됩니다</small></h3>
        <div class="fields">
          <label>단가 (원)<input v-model.number="edit.offer.unit_price" type="number" min="1" step="1" /></label>
          <label>지급기한 (일)<input v-model.number="edit.offer.payment_days" type="number" min="1" step="1" /></label>
          <label>계약 기간 (일)<input v-model.number="edit.offer.contract_days" type="number" min="1" step="1" /></label>
          <label>연동 약정<select v-model="edit.offer.price_linkage"><option :value="true">포함</option><option :value="false">미포함</option></select></label>
          <label class="check"><input v-model="edit.offer.linkage_waived_by_agreement" type="checkbox" />미연동 합의</label>
          <label>예상 계약금액<output>{{ view.derived_stale ? '갱신 대기' : won(offer?.contract_amount) }}</output></label>
        </div>
        <h3>협상 범위</h3>
        <div class="fields">
          <label>제시가<output>{{ won(edit.offer.unit_price) }}</output></label>
          <label>목표 합의가 (원)<input v-model.number="edit.offer.settlement" type="number" min="1" step="1" /></label>
          <label>최대 수용가 (원)<input v-model.number="edit.offer.walk_away" type="number" min="1" step="1" /></label>
        </div>
        <table class="grid">
          <thead><tr><th>구분</th><th>단가</th><th>목표 대비</th><th>시장 대비</th><th>직전 견적 대비</th><th>검사</th></tr></thead>
          <tbody><tr v-for="p in (offer?.price_range ?? [])" :key="p.name">
            <td>{{ p.label }}</td><td>{{ won(p.unit_price) }}</td>
            <template v-if="view.derived_stale"><td colspan="4">갱신 대기</td></template>
            <template v-else><td>{{ pct(p.vs_target_pct) }}</td><td>{{ pct(p.vs_market_pct) }}</td><td>{{ pct(p.vs_last_quote_pct) }}</td><td>{{ p.verdict_code }}</td></template>
          </tr></tbody>
        </table>
        <h3>양보 순서 <small>한 줄에 하나</small></h3>
        <textarea :value="edit.offer.concessions.join('\n')" rows="4" @input="edit.offer.concessions = ($event.target as HTMLTextAreaElement).value.split('\n').map(s => s.trim()).filter(Boolean)" />
        <h3>예상 쟁점과 대응</h3>
        <div v-for="(o, i) in edit.offer.objections" :key="i" class="pair">
          <label>쟁점<input v-model="o.issue" maxlength="300" /></label><label>대응<textarea v-model="o.response" rows="2" /></label>
        </div>
        <template v-if="edit.writing">
          <h3>협상 전략</h3><textarea v-model="edit.writing.strategy" rows="4" />
          <h3>제안 멘트 (초안)</h3><textarea v-model="edit.writing.opening_message" rows="3" />
        </template>
        <h3>납품·비고 <small>외부 제안서의 납품·비고와 같은 값입니다</small></h3>
        <textarea v-model="edit.offer.notes" rows="2" maxlength="500" />
        <details><summary>산정 근거와 자동 처리 (읽기 전용)</summary>
          <ul><li v-for="(r, i) in (offer?.rationale ?? [])" :key="'r' + i">{{ r.note }}</li></ul>
          <ul><li v-for="(w, i) in (offer?.warnings ?? [])" :key="'w' + i">확인 필요: {{ w.message ?? w.code }}</li></ul>
          <ul><li v-for="(l, i) in (offer?.law_refs ?? [])" :key="'l' + i">근거 조문: {{ l.law }} {{ l.article_no }}{{ l.paragraph ?? '' }}</li></ul>
          <ul><li v-for="(a, i) in (offer?.adjustments ?? [])" :key="'a' + i">조정: {{ a }}</li></ul>
          <ul><li v-for="(f, i) in (proposal.filled ?? [])" :key="'f' + i">자동 입력: {{ f.field }} = {{ f.value }} ({{ f.source }})</li></ul>
          <ul><li v-for="(c, i) in (proposal.considered ?? [])" :key="'c' + i">비교 후보: {{ c.label }} {{ won(c.unit_price) }} — {{ c.reason }}</li></ul>
        </details>
        <button type="button" class="btn" disabled title="다음 단계에서 제공합니다">내부 전략서 PDF (준비 중)</button>
      </fieldset>

      <fieldset v-show="tab === 'external'" role="tabpanel" :disabled="!!busy || view.status !== 'ACTIVE'" class="tabpanel">
        <p class="note">이 탭은 외부 제안서 전용 응답으로만 그립니다. 내부 협상 정보(목표 합의가·최대 수용가·양보 순서 등)는 들어가지 않습니다.</p>
        <template v-if="external">
          <table class="grid"><tbody>
            <tr><th>문서</th><td>{{ external.external_document.document_id }} · v{{ external.external_document.version }}</td></tr>
            <tr><th>수신</th><td>{{ external.external_document.supplier_name }} ({{ external.external_document.supplier_id }})</td></tr>
            <tr><th>품목</th><td>{{ external.external_document.rfq_id }} {{ external.external_document.part_name }}</td></tr>
            <tr><th>단가 · 물량</th><td>{{ won(external.external_document.unit_price) }} · {{ external.external_document.annual_volume }}개</td></tr>
            <tr><th>계약 기간 · 금액</th><td>{{ external.external_document.contract_days }}일 · {{ view.derived_stale ? '갱신 대기' : won(external.external_document.contract_amount) }}</td></tr>
            <tr><th>지급 · 연동</th><td>{{ external.external_document.payment_days }}일 · {{ external.external_document.price_linkage ? '연동 포함' : '연동 미포함' }}</td></tr>
          </tbody></table>
          <p class="note">공통 조건은 내부 전략서 탭에서 수정합니다.</p>
          <div v-if="edit.external" class="fields">
            <label class="wide">제목<input v-model="edit.external.title" maxlength="200" /></label>
            <label>회신 기한<input v-model="edit.external.reply_by" type="date" /></label>
            <label class="wide">인사말<textarea v-model="edit.external.cover_letter" rows="5" maxlength="1500" /></label>
            <label v-for="k in LINKAGE_KEYS" :key="k">연동 조항 · {{ k }}<input v-model="edit.external.linkage_terms[k]" maxlength="300" /></label>
          </div>
          <div class="downloads">
            <button v-for="f in view.available_export_formats.external" :key="f" type="button" class="btn" :disabled="!!busy" @click="download(f)">
              {{ busy === `export-${f}` ? '파일 만드는 중…' : `${f.toUpperCase()} 받기` }}
            </button>
            <span v-for="(b, f) in exports" :key="f" class="note">{{ String(f).toUpperCase() }} r{{ b.proposal_revision }}: {{ b.validation_snapshot.status === 'checked' ? badge(b.validation_snapshot) : '수정 후 미검사 초안' }}</span>
          </div>
        </template>
      </fieldset>

      <div class="actions">
        <label class="wide">변경 사유 (선택)<input v-model="changeNote" maxlength="500" :disabled="!!busy" /></label>
        <button type="button" class="btn btn-primary" :disabled="!dirty || !!busy || view.status !== 'ACTIVE'" @click="save">{{ busy === 'save' ? '저장 중…' : '수정 저장' }}</button>
        <button type="button" class="btn" :disabled="!!busy || view.status !== 'ACTIVE'" @click="recheck">{{ busy === 'check' ? '검사 중…' : '현재 버전 재검사' }}</button>
        <button v-if="conflictRevision" type="button" class="btn" @click="reload">최신 버전 불러오기</button>
      </div>
    </template>
    <p v-if="busy" role="status" class="note">처리 중입니다…</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>

    <details v-if="view.edit_history.length"><summary>편집 이력 ({{ view.edit_history.length }})</summary>
      <article v-for="h in view.edit_history" :key="h.to_revision">
        <p>r{{ h.from_revision }}→r{{ h.to_revision }} · {{ new Date(h.edited_at).toLocaleString('ko-KR') }} · {{ String(h.edited_by).slice(0, 8) }}{{ h.change_note ? ` · ${h.change_note}` : '' }}</p>
        <ul><li v-for="c in h.changes" :key="c.path">{{ c.path }}: {{ historyLabel(c.before) }} → {{ historyLabel(c.after) }}</li></ul>
      </article>
    </details>
  </section>
</template>

<style scoped>
.proposal{display:flex;flex-direction:column;gap:12px}.head{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.badge{padding:4px 10px;border-radius:999px;background:var(--surface-2);font-size:13px}.badge.recheck_required,.badge.failed{color:var(--danger)}
.tabs{display:flex;gap:8px;border-bottom:1px solid var(--border)}.tabs button{padding:10px 14px;border:0;background:none;color:var(--text);cursor:pointer;border-bottom:2px solid transparent}.tabs button.on{border-bottom-color:currentColor;font-weight:600}
.tabpanel{border:0;padding:0;margin:0;display:flex;flex-direction:column;gap:10px}.fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.fields label,.pair label,.actions label{display:flex;flex-direction:column;gap:6px;font-size:13px}.fields .check{flex-direction:row;align-items:center}.wide{grid-column:1/-1}
input,select,textarea{padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text);min-width:0;font:inherit}textarea{width:100%;box-sizing:border-box}
.grid{width:100%;border-collapse:collapse;font-size:13px}.grid th,.grid td{border-bottom:1px solid var(--border);padding:6px;text-align:left}.pair{display:grid;grid-template-columns:1fr 2fr;gap:10px}
.actions,.downloads{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}.note{color:var(--text-muted,inherit);font-size:13px}.error{color:var(--danger)}h3{margin:12px 0 0}h3 small,.tabs small{font-weight:400;font-size:12px;opacity:.75}
@media(max-width:800px){.fields,.pair{grid-template-columns:1fr}}
</style>
