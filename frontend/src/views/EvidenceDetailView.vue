<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { getSavedResponse } from '@/api/conversations'
import type { Citation, Evidence, GenerationResponse } from '@/api/types'
import ErrorNotice from '@/components/ErrorNotice.vue'
import { isApiError } from '@/utils/errors'
import {
  evidenceKindLabel,
  findToolCall,
  isHttpUrl,
  isStubResponse,
  parseEvidenceFields,
  parseSupplierEvidence,
  toolSignature,
} from '@/utils/evidence'
import { NO_DATA, formatHeld, formatInteger, formatKrw, formatPercent, formatPpm } from '@/utils/format'
import { provenanceLabel } from '@/utils/provenance'

// 새 탭에서 URL의 응답 ID로 저장된 질문과 전체 응답을 조회한다
const props = defineProps<{
  conversationId: string
  responseId: string
  evidenceId: string
}>()

const route = useRoute()

const response = ref<GenerationResponse | null>(null)
const evidence = ref<Evidence | null>(null)
const citation = ref<Citation | null>(null)
const question = ref<string | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)
const notFound = ref(false)
let loadEpoch = 0
let controller: AbortController | null = null
onBeforeUnmount(() => { loadEpoch += 1; controller?.abort() })

async function load() {
  const token = ++loadEpoch
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = null
  notFound.value = false
  response.value = null
  evidence.value = null
  try {
    const saved = await getSavedResponse(props.conversationId, props.responseId, controller.signal)
    if (token !== loadEpoch) return
    const res = saved.response
    const found = res?.answer.evidence.find((e) => e.id === props.evidenceId) ?? null
    if (!res || !found) {
      notFound.value = true
      return
    }
    response.value = res
    evidence.value = found
    citation.value = res.answer.citations.find((c) => c.evidence_id === found.id) ?? null
    question.value = saved.query
  } catch (e) {
    if (token === loadEpoch) error.value = e
  } finally {
    if (token === loadEpoch) loading.value = false
  }
}

watch(() => [props.conversationId, props.responseId, props.evidenceId], load, { immediate: true })

// 같은 앱의 대화와 협력사 화면 주소만 허용한다.
const backPath = computed(() => {
  const v = route.query.back
  const s = Array.isArray(v) ? v[0] : v
  return typeof s === 'string' && /^\/(chat|suppliers)(?:[/?]|$)/.test(s) && !s.includes('\\') ? s : '/chat'
})
const rfq = computed(() => {
  const v = route.query.rfq
  const s = Array.isArray(v) ? v[0] : v
  return typeof s === 'string' && s ? s : null
})

const supplier = computed(() => (evidence.value ? parseSupplierEvidence(evidence.value) : null))
const toolCall = computed(() => (response.value && evidence.value ? findToolCall(response.value, evidence.value) : null))
const stub = computed(() => (response.value ? isStubResponse(response.value, evidence.value) || supplier.value?.stub === true : false))

const mainCapability = computed(() => {
  const caps = supplier.value?.capabilities ?? []
  return caps.reduce<(typeof caps)[number] | null>((best, c) => (!best || c.monthly_capacity > best.monthly_capacity ? c : best), null)
})
const latestQuote = computed(() => {
  const quotes = [...(supplier.value?.recent_quotes ?? [])].sort((a, b) => b.quoted_at.localeCompare(a.quoted_at))
  return quotes[0] ?? null
})

/** AI가 응답에 쓴 값을 필드 단위로 보여 준다 */
const supplierRows = computed(() => {
  const s = supplier.value
  if (!s) return []
  const profile = provenanceLabel(s.provenance.profile)
  const certifications = provenanceLabel(s.provenance.certifications)
  const capabilities = provenanceLabel(s.provenance.capabilities)
  const quality = provenanceLabel(s.provenance.quality)
  const contracts = provenanceLabel(s.provenance.contracts)
  return [
    { item: '협력사 ID', value: s.supplier_id, source: profile, use: '대상 식별' },
    { item: '기업명', value: s.name ?? NO_DATA, source: profile, use: '대상 식별' },
    { item: '지역', value: s.region ?? NO_DATA, source: profile, use: '공급 지역 확인' },
    { item: '공정 / 소재', value: mainCapability.value ? `${mainCapability.value.process} / ${mainCapability.value.material}` : NO_DATA, source: capabilities, use: 'RFQ 조건 비교' },
    { item: '월 생산능력', value: mainCapability.value ? `${formatInteger(mainCapability.value.monthly_capacity)}개` : NO_DATA, source: capabilities, use: '공급능력 근거' },
    { item: 'IATF 16949', value: formatHeld(s.iatf16949), source: certifications, use: '필수 인증 확인' },
    { item: 'ISO 14001', value: formatHeld(s.iso14001), source: certifications, use: '환경 인증 확인' },
    { item: '불량률', value: formatPpm(s.quality.defect_ppm), source: quality, use: '품질 비교' },
    { item: '납기 준수율', value: formatPercent(s.quality.on_time_delivery_rate), source: quality, use: '납기 평가' },
    { item: '최근 견적', value: latestQuote.value ? formatKrw(latestQuote.value.unit_price_krw) : NO_DATA, source: provenanceLabel(latestQuote.value?.source), use: '가격 비교' },
    { item: '대금 지급 기한', value: s.existing_contract?.payment_days == null ? NO_DATA : `${formatInteger(s.existing_contract.payment_days)}일`, source: contracts, use: '계약 조건 확인' },
    { item: '납품대금 연동', value: s.existing_contract?.price_linkage == null ? NO_DATA : s.existing_contract.price_linkage ? '적용' : '미적용', source: contracts, use: '계약 조건 확인' },
  ]
})

const fieldRows = computed(() => (evidence.value && !supplier.value ? parseEvidenceFields(evidence.value) : null))

const target = computed(() => {
  if (supplier.value) return supplier.value.supplier_id
  const supplierId = evidence.value?.meta.supplier_id
  if (typeof supplierId === 'string') return supplierId
  const article = evidence.value?.meta.article_no
  return typeof article === 'string' ? article : evidence.value?.label ?? NO_DATA
})

const effectiveDate = computed(() => {
  const v = evidence.value?.meta.effective_date
  return typeof v === 'string' ? v : null
})
</script>

<template>
  <div class="evidence-page">
    <header class="ev-header">
      <div>
        <nav class="crumbs muted" aria-label="위치">NegoPilot / AI 대화 / 근거 자료</nav>
        <h1>근거 자료 상세</h1>
      </div>
      <RouterLink :to="backPath" class="btn btn-outline-primary back">← 원래 화면으로 돌아가기</RouterLink>
    </header>

    <main class="ev-body">
      <p v-if="loading" aria-live="polite">근거 자료를 불러오는 중…</p>

      <section v-else-if="error" class="panel">
        <ErrorNotice
          :error="error"
          :message="
            isApiError(error, 'CONVERSATION_NOT_FOUND')
              ? '대화가 삭제됐거나 접근 권한이 없습니다. 이 대화를 만든 계정으로 확인해 주세요.'
              : null
          "
          :retry="load"
        />
      </section>

      <section v-else-if="notFound" class="panel panel-pad">이 대화에서 해당 근거를 찾을 수 없습니다.</section>

      <template v-else-if="response && evidence">
        <section class="panel panel-pad source-card" aria-labelledby="ev-title">
          <div>
            <h2 id="ev-title">AI 답변의 출처: {{ evidenceKindLabel(evidence) }}</h2>
            <p class="ev-sub">
              {{ supplier?.name ?? evidence.label }}
              <template v-if="supplier"> · {{ supplier.supplier_id }}</template>
              <template v-if="rfq"> · {{ rfq }}</template>
            </p>
            <span class="badge">{{ stub ? 'STUB 테스트 실행' : 'LIVE Agent 실행' }}</span>
            <p v-if="evidence.source === 'business'" class="sub">실제 모델과 도구를 실행해도 가상 데이터나 관리자 입력이 포함될 수 있습니다. 항목별 출처를 확인하세요.</p>
          </div>
          <p v-if="toolCall" class="tool muted">근거 도구 {{ toolSignature(toolCall) }}</p>
        </section>

        <div class="ev-grid">
          <section class="panel panel-pad" aria-labelledby="raw-h">
            <div class="panel-title">
              <div>
                <h2 id="raw-h">원본 조회 데이터</h2>
                <p class="sub">AI가 응답에 사용한 값을 필드 단위로 표시합니다.</p>
              </div>
            </div>

            <template v-if="supplier">
              <div class="kpi-row">
                <div class="kpi">
                  <div class="label">월 생산능력</div>
                  <div class="value">{{ mainCapability ? `${formatInteger(mainCapability.monthly_capacity)}개` : NO_DATA }}</div>
                </div>
                <div class="kpi">
                  <div class="label">불량률</div>
                  <div class="value">{{ formatPpm(supplier.quality?.defect_ppm) }}</div>
                </div>
                <div class="kpi">
                  <div class="label">납기 준수율</div>
                  <div class="value">{{ formatPercent(supplier.quality?.on_time_delivery_rate) }}</div>
                </div>
              </div>
              <h3 class="table-title">기업과 생산 및 품질 데이터</h3>
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr><th>항목</th><th>조회값</th><th>출처</th><th>AI 답변 활용</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="r in supplierRows" :key="r.item">
                      <td class="item">{{ r.item }}</td>
                      <td>{{ r.value }}</td>
                      <td>{{ r.source }}</td>
                      <td>{{ r.use }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <template v-if="supplier.financials.length">
                <h3 class="table-title">재무 데이터</h3>
                <div class="table-wrap">
                  <table>
                    <thead><tr><th>사업연도</th><th>재무제표</th><th>매출액</th><th>영업이익</th><th>부채비율</th><th>출처</th></tr></thead>
                    <tbody>
                      <tr v-for="(row, index) in supplier.financials" :key="`${row.year}-${row.statement_type}-${index}`">
                        <td>{{ row.year }}</td><td>{{ row.statement_type ?? NO_DATA }}</td>
                        <td>{{ formatKrw(row.revenue_krw) }}</td><td>{{ formatKrw(row.operating_profit_krw) }}</td>
                        <td>{{ formatPercent(row.debt_ratio) }}</td><td>{{ provenanceLabel(row.source) }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </template>
            </template>

            <template v-else-if="fieldRows">
              <h3 class="table-title">{{ evidence.label }}{{ stub ? ' (시연)' : '' }}</h3>
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr><th>항목</th><th>조회값</th><th>출처</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="r in fieldRows" :key="r.key">
                      <td class="item">{{ r.label }}</td>
                      <td>{{ r.value }}</td>
                      <td>{{ provenanceLabel(r.source) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
            <template v-else>
              <h3 class="table-title">{{ evidence.label }}</h3>
              <pre class="raw-text">{{ evidence.text }}</pre>
            </template>
            <details v-if="supplier || fieldRows" class="raw-details">
              <summary>원본 조회 응답 보기</summary>
              <pre class="raw-text">{{ evidence.text }}</pre>
            </details>
          </section>

          <section class="panel panel-pad" aria-labelledby="used-h">
            <h2 id="used-h" class="side-title">AI 답변에서 사용한 근거</h2>
            <blockquote class="quote">
              <span v-if="question" class="muted">질문: {{ question }}</span>
              “{{ response.answer.text }}”
            </blockquote>

            <h3 class="side-sub">조회와 출처 정보</h3>
            <dl class="kv">
              <dt>조회 도구</dt><dd>{{ toolCall?.tool ?? NO_DATA }}</dd>
              <dt>실행 모드</dt><dd>{{ stub ? 'STUB 테스트 실행' : 'LIVE 모델과 도구 실행' }}</dd>
              <dt>데이터 출처</dt><dd>{{ evidence.source === 'business' ? '항목별 출처 참조' : stub ? 'STUB 테스트 응답' : '국가법령정보센터 원문 색인' }}</dd>
              <template v-if="supplier">
                <dt>기본 정보 출처</dt><dd>{{ provenanceLabel(supplier.provenance.profile) }}</dd>
                <dt>기본 정보 원출처</dt><dd>{{ provenanceLabel(supplier.provenance.profile_origin) }}</dd>
                <dt>재무 출처</dt><dd>{{ provenanceLabel(supplier.provenance.financials) }}</dd>
              </template>
              <dt>자료 성격</dt><dd>{{ evidence.source === 'law' ? '법령 조문' : '구조화된 기업 DB 필드' }}</dd>
              <template v-if="effectiveDate"><dt>시행일</dt><dd>{{ effectiveDate }}</dd></template>
              <dt>외부 원문 링크</dt>
              <dd>
                <a v-if="isHttpUrl(evidence.source_url)" :href="evidence.source_url" target="_blank" rel="noopener noreferrer">원문 보기 ↗</a>
                <template v-else>제공되지 않음</template>
              </dd>
              <dt>조회 대상</dt><dd>{{ target }}</dd>
              <template v-if="citation"><dt>답변 인용 번호</dt><dd>[{{ citation.n }}]</dd></template>
            </dl>
          </section>
        </div>

        <p class="info-bar" role="note">
          ⓘ 이 화면은 AI 답변이 인용한 근거를 독립된 새 탭에서 확인하는 화면입니다.
          {{ evidence.source === 'business' ? 'DART 공시와 MOCK 가상 데이터 및 관리자 입력을 구분해 확인하세요. 출처 정보가 없는 값은 실제 데이터로 확인된 값이 아닙니다.' : '' }}
        </p>
      </template>
    </main>
  </div>
</template>

<style scoped>
.evidence-page {
  min-height: 100vh;
  background: var(--bg);
}
.ev-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px 24px;
  padding: 22px 32px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}
.crumbs {
  margin-bottom: 6px;
}
.ev-header h1 {
  font-size: 24px;
}
.back {
  min-width: 240px;
  justify-content: flex-start;
}
.ev-body {
  max-width: 1440px;
  padding: 24px 32px 40px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.source-card {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
}
.source-card h2 {
  font-size: 17px;
}
.ev-sub {
  margin: 6px 0 12px;
  font-size: 13px;
}
.tool {
  margin: 0;
}
.ev-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(280px, 1fr);
  gap: 16px;
  align-items: start;
}
.table-title {
  font-size: 14px;
  margin: 22px 0 10px;
}
.item {
  font-weight: 600;
}
.raw-text {
  margin: 0;
  padding: 14px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}
.raw-details { margin-top: 20px; }
.raw-details summary { cursor: pointer; margin-bottom: 10px; }
.side-title {
  font-size: 16px;
  margin-bottom: 14px;
}
.quote {
  margin: 0;
  padding: 16px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 12px;
  line-height: 1.6;
  display: flex;
  flex-direction: column;
  gap: 6px;
  white-space: pre-line;
}
.side-sub {
  font-size: 14px;
  margin: 20px 0 6px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}
.kv {
  margin: 0;
  display: grid;
  grid-template-columns: 120px 1fr;
}
.kv dt,
.kv dd {
  margin: 0;
  padding: 11px 0;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}
.kv dt {
  color: var(--text-3);
}
.kv dd {
  font-weight: 600;
}
.info-bar {
  margin: 0;
  padding: 12px 16px;
  border-radius: var(--radius);
  background: var(--primary-soft);
  color: var(--primary);
  font-size: 12px;
}
@media (max-width: 900px) {
  .ev-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
