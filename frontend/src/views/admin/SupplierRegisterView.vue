<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { AUDIT_GRADES, MATERIALS, PROCESSES, type Process, REGIONS, SEED_SUPPLIER_MAX } from '@/data/dataCatalog'
import { downloadJson, useDataRequests } from '@/stores/dataRequests'
import {
  emptyCapability,
  emptyFinancial,
  emptyQuote,
  emptySupplierForm,
  maskBizNo,
  supplierPayload,
  validateSupplier,
} from '@/utils/supplierForm'

const requests = useDataRequests()
const thisYear = new Date().getFullYear()

const takenIds = computed(() => {
  const ids = new Set<string>()
  for (let n = 1; n <= SEED_SUPPLIER_MAX; n++) ids.add(`SUP-${String(n).padStart(4, '0')}`)
  requests.state.items.filter((r) => r.kind === 'supplier').forEach((r) => ids.add(r.target))
  return ids
})
function nextId(): string {
  let n = SEED_SUPPLIER_MAX + 1
  while (takenIds.value.has(`SUP-${String(n).padStart(4, '0')}`)) n++
  return `SUP-${String(n).padStart(4, '0')}`
}

const form = reactive(emptySupplierForm(nextId()))
const tried = ref(false)
const done = ref<string | null>(null)
const showJson = ref(false)

const errors = computed(() => validateSupplier(form, takenIds.value, thisYear))
const errorCount = computed(() => Object.keys(errors.value).length)
function err(key: string): string | undefined {
  return tried.value ? errors.value[key] : undefined
}
const payload = computed(() => supplierPayload(form))
const maskedBizNo = computed(() => maskBizNo(form.bizNo))

const tableCounts = computed(() => [
  { table: 'suppliers', rows: 1 },
  { table: 'capabilities', rows: form.capabilities.length },
  { table: 'quality', rows: 1 },
  { table: 'contracts', rows: form.hasContract ? 1 : 0 },
  { table: 'quotes', rows: form.quotes.length },
  { table: 'financials', rows: form.financials.length },
])

function materialsFor(process: string): string[] {
  return MATERIALS[process as Process] ?? []
}

function submit() {
  tried.value = true
  done.value = null
  if (errorCount.value) {
    document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
    return
  }
  const p = payload.value
  requests.add('supplier', p.suppliers.supplier_id, `${p.suppliers.name} · ${p.capabilities.map((c) => `${c.process} ${c.material}`).join(', ')}`, p)
  done.value = `${p.suppliers.name}(${p.suppliers.supplier_id}) 등록 요청을 저장했습니다.`
  Object.assign(form, emptySupplierForm(nextId()))
  tried.value = false
  window.scrollTo({ top: 0 })
}

function reset() {
  if (!window.confirm('입력한 내용을 모두 지울까요?')) return
  Object.assign(form, emptySupplierForm(nextId()))
  tried.value = false
}
</script>

<template>
  <main class="page-body">
    <p class="intro">
      business-mcp DB에 새 협력사를 넣을 정보를 입력합니다. 저장 형식은 표(schema.sql) 열 이름 그대로 만들어집니다.
    </p>
    <p v-if="done" class="message" role="status">
      {{ done }} <RouterLink :to="{ name: 'admin-data' }">등록 요청 목록 보기 →</RouterLink>
    </p>

    <div class="layout">
      <form class="sections" novalidate @submit.prevent="submit">
        <!-- 기본 정보 -->
        <section class="panel panel-pad" aria-labelledby="basic-h">
          <div class="panel-title">
            <div>
              <h2 id="basic-h">기본 정보</h2>
              <p class="sub">suppliers 표 · DART 기업은 공시 값, MOCK은 실습용 가상 기업</p>
            </div>
          </div>
          <div class="grid g3">
            <div class="field">
              <label for="r-id">협력사 ID *</label>
              <input id="r-id" v-model="form.supplierId" type="text" :aria-invalid="!!err('supplierId')" />
              <span v-if="err('supplierId')" class="field-error">{{ err('supplierId') }}</span>
            </div>
            <div class="field">
              <label for="r-source">기본 정보 출처 *</label>
              <select id="r-source" v-model="form.source">
                <option value="MOCK">MOCK (가상 기업)</option>
                <option value="DART">DART (공시 기업)</option>
              </select>
            </div>
            <div class="field">
              <label for="r-corp">DART 고유번호{{ form.source === 'DART' ? ' *' : '' }}</label>
              <input id="r-corp" v-model="form.corpCode" type="text" inputmode="numeric" maxlength="8" :disabled="form.source !== 'DART'" placeholder="8자리" :aria-invalid="!!err('corpCode')" />
              <span v-if="err('corpCode')" class="field-error">{{ err('corpCode') }}</span>
            </div>

            <div class="field span2">
              <label for="r-name">협력사명 *</label>
              <input id="r-name" v-model="form.name" type="text" maxlength="100" placeholder="예: 한빛정밀" :aria-invalid="!!err('name')" />
              <span v-if="err('name')" class="field-error">{{ err('name') }}</span>
            </div>
            <div class="field">
              <label for="r-region">지역 *</label>
              <select id="r-region" v-model="form.region" :aria-invalid="!!err('region')">
                <option value="">선택</option>
                <option v-for="r in REGIONS" :key="r" :value="r">{{ r }}</option>
              </select>
              <span v-if="err('region')" class="field-error">{{ err('region') }}</span>
            </div>

            <div class="field">
              <label for="r-founded">설립연도 *</label>
              <input id="r-founded" v-model="form.foundedYear" type="text" inputmode="numeric" maxlength="4" placeholder="예: 2004" :aria-invalid="!!err('foundedYear')" />
              <span v-if="err('foundedYear')" class="field-error">{{ err('foundedYear') }}</span>
            </div>
            <div class="field">
              <label for="r-biz">사업자등록번호 *</label>
              <input id="r-biz" v-model="form.bizNo" type="text" autocomplete="off" placeholder="123-45-67890" :aria-invalid="!!err('bizNo')" />
              <span v-if="err('bizNo')" class="field-error">{{ err('bizNo') }}</span>
              <span v-else-if="maskedBizNo" class="hint">저장값 {{ maskedBizNo }} (뒤 5자리는 저장하지 않음)</span>
            </div>
            <div class="field">
              <label for="r-emp">직원 수</label>
              <input id="r-emp" v-model="form.employees" type="text" inputmode="numeric" placeholder="모르면 비움" :aria-invalid="!!err('employees')" />
              <span v-if="err('employees')" class="field-error">{{ err('employees') }}</span>
            </div>

            <div class="field">
              <label for="r-rev">최근 매출 (억 원)</label>
              <input id="r-rev" v-model="form.revenueEok" type="text" inputmode="numeric" placeholder="예: 820" :aria-invalid="!!err('revenueEok')" />
              <span v-if="err('revenueEok')" class="field-error">{{ err('revenueEok') }}</span>
            </div>
            <div class="field">
              <label for="r-stock">종목코드</label>
              <input id="r-stock" v-model="form.stockCode" type="text" inputmode="numeric" maxlength="6" placeholder="상장사만" :aria-invalid="!!err('stockCode')" />
              <span v-if="err('stockCode')" class="field-error">{{ err('stockCode') }}</span>
            </div>
            <div class="field">
              <label for="r-ksic">업종 코드 (KSIC)</label>
              <input id="r-ksic" v-model="form.indutyCode" type="text" inputmode="numeric" maxlength="5" placeholder="예: 30332" :aria-invalid="!!err('indutyCode')" />
              <span v-if="err('indutyCode')" class="field-error">{{ err('indutyCode') }}</span>
            </div>
          </div>

          <fieldset class="certs">
            <legend>인증</legend>
            <label class="check"><input v-model="form.iatf16949" type="checkbox" /> IATF 16949</label>
            <label class="check"><input v-model="form.iso14001" type="checkbox" /> ISO 14001</label>
          </fieldset>
        </section>

        <!-- 생산능력 -->
        <section class="panel panel-pad" aria-labelledby="cap-h">
          <div class="panel-title">
            <div>
              <h2 id="cap-h">생산능력 <span class="req">*</span></h2>
              <p class="sub">capabilities 표 · 공정·소재 조합마다 한 줄. 협력사 검색은 이 표로 찾습니다.</p>
            </div>
            <button class="btn" type="button" @click="form.capabilities.push(emptyCapability())">+ 공정 추가</button>
          </div>
          <p v-if="err('capabilities')" class="field-error">{{ err('capabilities') }}</p>
          <div class="table-wrap">
            <table class="input-table">
              <thead><tr><th>공정</th><th>소재</th><th>월 생산능력 (개)</th><th><span class="sr-only">삭제</span></th></tr></thead>
              <tbody>
                <tr v-for="(c, i) in form.capabilities" :key="i">
                  <td>
                    <select v-model="c.process" :aria-label="`${i + 1}번째 공정`" :aria-invalid="!!err(`cap.${i}.process`)">
                      <option value="">선택</option>
                      <option v-for="p in PROCESSES" :key="p" :value="p">{{ p }}</option>
                    </select>
                  </td>
                  <td>
                    <input v-model="c.material" type="text" :list="`cap-mat-${i}`" :aria-label="`${i + 1}번째 소재`" placeholder="예: ADC12" :aria-invalid="!!err(`cap.${i}.material`)" />
                    <datalist :id="`cap-mat-${i}`"><option v-for="m in materialsFor(c.process)" :key="m" :value="m" /></datalist>
                    <span v-if="err(`cap.${i}.material`)" class="field-error">{{ err(`cap.${i}.material`) }}</span>
                  </td>
                  <td>
                    <input v-model="c.monthlyCapacity" type="text" inputmode="numeric" :aria-label="`${i + 1}번째 월 생산능력`" placeholder="예: 40,000" :aria-invalid="!!err(`cap.${i}.monthlyCapacity`)" />
                  </td>
                  <td class="del">
                    <button class="link-btn" type="button" :disabled="form.capabilities.length === 1" @click="form.capabilities.splice(i, 1)">삭제</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- 품질·납기 -->
        <section class="panel panel-pad" aria-labelledby="q-h">
          <div class="panel-title">
            <div>
              <h2 id="q-h">품질·납기 실적 <span class="req">*</span></h2>
              <p class="sub">quality 표 · 이 값이 없으면 협력사 요약(supplier_overview)에 나오지 않습니다.</p>
            </div>
          </div>
          <div class="grid g4">
            <div class="field">
              <label for="r-ppm">불량률 (ppm) *</label>
              <input id="r-ppm" v-model="form.defectPpm" type="text" inputmode="numeric" placeholder="예: 450" :aria-invalid="!!err('defectPpm')" />
              <span v-if="err('defectPpm')" class="field-error">{{ err('defectPpm') }}</span>
            </div>
            <div class="field">
              <label for="r-ontime">납기 준수율 (%) *</label>
              <input id="r-ontime" v-model="form.onTimeRatePct" type="text" inputmode="decimal" placeholder="예: 97.5" :aria-invalid="!!err('onTimeRatePct')" />
              <span v-if="err('onTimeRatePct')" class="field-error">{{ err('onTimeRatePct') }}</span>
              <span v-else-if="payload.quality.on_time_rate !== null" class="hint">저장값 {{ payload.quality.on_time_rate }}</span>
            </div>
            <div class="field">
              <label for="r-grade">품질 감사 등급 *</label>
              <select id="r-grade" v-model="form.auditGrade" :aria-invalid="!!err('auditGrade')">
                <option value="">선택</option>
                <option v-for="g in AUDIT_GRADES" :key="g" :value="g">{{ g }}</option>
              </select>
              <span v-if="err('auditGrade')" class="field-error">{{ err('auditGrade') }}</span>
            </div>
            <div class="field">
              <label for="r-years">거래 기간 (년) *</label>
              <input id="r-years" v-model="form.yearsDealing" type="text" inputmode="numeric" :aria-invalid="!!err('yearsDealing')" />
              <span v-if="err('yearsDealing')" class="field-error">{{ err('yearsDealing') }}</span>
              <span v-else class="hint">신규 후보는 0</span>
            </div>
          </div>
        </section>

        <!-- 기존 거래 조건 -->
        <section class="panel panel-pad" aria-labelledby="ct-h">
          <div class="panel-title">
            <div>
              <h2 id="ct-h">기존 거래 조건</h2>
              <p class="sub">contracts 표 · 거래 이력이 있는 협력사만. 법규 검토(지급기한·연동제)에 쓰입니다.</p>
            </div>
            <label class="check"><input v-model="form.hasContract" type="checkbox" /> 기존 거래 있음</label>
          </div>
          <div v-if="form.hasContract" class="grid g4">
            <div class="field">
              <label for="r-pay">대금 지급기한 (일) *</label>
              <input id="r-pay" v-model="form.paymentDays" type="text" inputmode="numeric" placeholder="예: 60" :aria-invalid="!!err('paymentDays')" />
              <span v-if="err('paymentDays')" class="field-error">{{ err('paymentDays') }}</span>
            </div>
            <div class="field">
              <label for="r-raw">원재료 비중 (%) *</label>
              <input id="r-raw" v-model="form.rawMaterialPct" type="text" inputmode="decimal" placeholder="예: 40" :aria-invalid="!!err('rawMaterialPct')" />
              <span v-if="err('rawMaterialPct')" class="field-error">{{ err('rawMaterialPct') }}</span>
            </div>
            <label class="check box"><input v-model="form.priceLinkage" type="checkbox" /> 납품대금 연동 약정</label>
            <label class="check box"><input v-model="form.autoRenewal" type="checkbox" /> 자동 갱신</label>
          </div>
          <p v-else class="muted">신규 후보는 비워 둡니다.</p>
        </section>

        <!-- 견적 이력 -->
        <section class="panel panel-pad" aria-labelledby="qt-h">
          <div class="panel-title">
            <div>
              <h2 id="qt-h">견적 이력</h2>
              <p class="sub">quotes 표 · 단가 비교 근거. 선택 입력</p>
            </div>
            <button class="btn" type="button" @click="form.quotes.push(emptyQuote())">+ 견적 추가</button>
          </div>
          <div v-if="form.quotes.length" class="table-wrap">
            <table class="input-table">
              <thead><tr><th>공정</th><th>소재</th><th>개당 단가 (원)</th><th>견적일</th><th><span class="sr-only">삭제</span></th></tr></thead>
              <tbody>
                <tr v-for="(q, i) in form.quotes" :key="i">
                  <td>
                    <select v-model="q.process" :aria-label="`${i + 1}번째 견적 공정`" :aria-invalid="!!err(`quote.${i}.process`)">
                      <option value="">선택</option>
                      <option v-for="p in PROCESSES" :key="p" :value="p">{{ p }}</option>
                    </select>
                  </td>
                  <td><input v-model="q.material" type="text" :aria-label="`${i + 1}번째 견적 소재`" :aria-invalid="!!err(`quote.${i}.material`)" /></td>
                  <td><input v-model="q.unitPrice" type="text" inputmode="numeric" :aria-label="`${i + 1}번째 견적 단가`" placeholder="예: 3,900" :aria-invalid="!!err(`quote.${i}.unitPrice`)" /></td>
                  <td><input v-model="q.quotedAt" type="date" :aria-label="`${i + 1}번째 견적일`" :aria-invalid="!!err(`quote.${i}.quotedAt`)" /></td>
                  <td class="del"><button class="link-btn" type="button" @click="form.quotes.splice(i, 1)">삭제</button></td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="muted">견적 이력이 없습니다.</p>
        </section>

        <!-- 재무 -->
        <section class="panel panel-pad" aria-labelledby="fin-h">
          <div class="panel-title">
            <div>
              <h2 id="fin-h">연도별 재무</h2>
              <p class="sub">financials 표 · 단위 원. DART 기업은 단일회사 주요계정 값. 선택 입력</p>
            </div>
            <button class="btn" type="button" @click="form.financials.push(emptyFinancial())">+ 연도 추가</button>
          </div>
          <div v-if="form.financials.length" class="table-wrap">
            <table class="input-table fin">
              <thead>
                <tr><th>사업연도</th><th>구분</th><th>매출액</th><th>영업이익</th><th>당기순이익</th><th>자산총계</th><th>부채총계</th><th>자본총계</th><th><span class="sr-only">삭제</span></th></tr>
              </thead>
              <tbody>
                <tr v-for="(f, i) in form.financials" :key="i">
                  <td><input v-model="f.year" type="text" inputmode="numeric" maxlength="4" :aria-label="`${i + 1}번째 사업연도`" placeholder="2025" :aria-invalid="!!err(`fin.${i}.year`)" /></td>
                  <td>
                    <select v-model="f.fsDiv" :aria-label="`${i + 1}번째 재무제표 구분`">
                      <option value="OFS">별도</option>
                      <option value="CFS">연결</option>
                    </select>
                  </td>
                  <td v-for="k in (['revenue', 'operatingIncome', 'netIncome', 'totalAssets', 'totalLiabilities', 'totalEquity'] as const)" :key="k">
                    <input v-model="f[k]" type="text" inputmode="numeric" :aria-label="`${i + 1}번째 ${k}`" :aria-invalid="!!err(`fin.${i}.${k}`)" />
                  </td>
                  <td class="del"><button class="link-btn" type="button" @click="form.financials.splice(i, 1)">삭제</button></td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="muted">재무 자료가 없으면 협력사 상세의 재무 탭은 '정보 없음'으로 보입니다.</p>
        </section>
      </form>

      <!-- 저장 미리보기 -->
      <aside class="panel panel-pad preview" aria-labelledby="pv-h">
        <h2 id="pv-h">저장 미리보기</h2>
        <p class="sub muted">{{ form.name.trim() || '(협력사명)' }} · {{ form.supplierId }}</p>
        <table class="counts">
          <tbody>
            <tr v-for="t in tableCounts" :key="t.table">
              <td><code>{{ t.table }}</code></td>
              <td class="num">{{ t.rows }}행</td>
            </tr>
          </tbody>
        </table>
        <p v-if="tried && errorCount" class="field-error" role="alert">고칠 항목 {{ errorCount }}개가 있습니다.</p>
        <p v-else-if="!errorCount" class="ok">필수 항목을 모두 채웠습니다.</p>

        <button class="btn btn-primary wide" type="button" @click="submit">등록 요청 저장</button>
        <div class="row2">
          <button class="btn" type="button" @click="downloadJson(`${form.supplierId}.json`, payload)">JSON 내려받기</button>
          <button class="btn" type="button" @click="reset">초기화</button>
        </div>
        <button class="link-btn" type="button" :aria-expanded="showJson" @click="showJson = !showJson">
          {{ showJson ? '저장 형식 닫기' : '저장 형식(JSON) 보기' }}
        </button>
        <pre v-if="showJson" class="json">{{ JSON.stringify(payload, null, 2) }}</pre>
        <p class="footnote">
          등록 API가 아직 없어 DB에 바로 저장되지 않습니다. 등록 요청은 이 화면에만 남고, 내려받은 JSON으로 seed나 DB에 반영합니다.
        </p>
      </aside>
    </div>
  </main>
</template>

<style scoped>
.intro {
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
}
.message {
  margin: 0;
  padding: 10px 14px;
  font-size: 12px;
  background: var(--primary-soft);
  color: var(--primary);
  border-radius: var(--radius);
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 18px;
  align-items: start;
}
.sections {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-width: 0;
}
.grid {
  display: grid;
  gap: 14px 12px;
}
.g3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.g4 {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: start;
}
.span2 {
  grid-column: span 2;
}
.field > label {
  font-weight: 600;
  color: var(--text);
}
.req {
  color: var(--danger);
}
.hint {
  font-size: 11px;
  color: var(--text-3);
}
input:disabled {
  background: var(--surface-2);
  color: var(--text-3);
}
input[type='date'] {
  height: 38px;
  padding: 0 8px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  font: inherit;
  font-size: 13px;
  width: 100%;
}
.certs {
  display: flex;
  gap: 10px;
  margin: 16px 0 0;
  padding: 0;
  border: 0;
}
.certs legend {
  float: left;
  margin-right: 8px;
  font-size: 12px;
  font-weight: 600;
  line-height: 38px;
}
.check {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  background: var(--surface);
  font-size: 13px;
  cursor: pointer;
}
.check input {
  margin: 0;
  width: 16px;
  height: 16px;
  accent-color: var(--primary);
}
.check.box {
  margin-top: 21px;
}
.input-table td {
  padding: 6px;
  vertical-align: top;
}
.input-table input,
.input-table select {
  height: 34px;
}
.input-table.fin input {
  min-width: 96px;
}
.input-table tbody tr:nth-child(even) td {
  background: transparent;
}
.del {
  width: 48px;
  text-align: center;
  vertical-align: middle !important;
}
.link-btn:disabled {
  color: var(--text-3);
  cursor: not-allowed;
}
.preview {
  position: sticky;
  top: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.preview h2 {
  font-size: 16px;
}
.preview .sub {
  margin: -6px 0 0;
}
.counts td {
  height: 30px;
  padding: 4px 8px;
  font-size: 12px;
}
.counts code {
  font-size: 12px;
}
.ok {
  margin: 0;
  font-size: 12px;
  color: var(--success);
}
.preview .field-error {
  margin: 0;
}
.wide {
  width: 100%;
}
.row2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.preview .link-btn {
  align-self: flex-start;
}
.json {
  max-height: 320px;
  overflow: auto;
  margin: 0;
  padding: 10px;
  font-size: 11px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.footnote {
  margin: 0;
}
@media (max-width: 1100px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .preview {
    position: static;
  }
}
@media (max-width: 760px) {
  .g3,
  .g4 {
    grid-template-columns: minmax(0, 1fr);
  }
  .span2 {
    grid-column: auto;
  }
}
</style>
