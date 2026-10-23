import type {
  AdminSupplierDetail,
  AdminSupplierProfile,
  CapabilityInput,
  CreateSupplierRequest,
  ProfileInput,
  QualityInput,
  QuoteInput,
  SupplierSource,
} from '@/api/admin-suppliers'
import { AUDIT_GRADES, PROCESSES, REGIONS } from '@/data/dataCatalog'

// 협력사 입력 폼 ↔ 관리자 협력사 API 요청 (docs/contracts/admin-suppliers.md).
// 입력칸은 문자열로 두고, 보낼 때 API 필드 이름과 단위로 바꾼다.
// 납기 준수율은 화면에서 백분율(소수 첫째 자리까지)로 받고 0..1 비율(소수 셋째 자리까지)로 보낸다.
// 등록 화면과 상세 수정 화면이 영역별 검사와 변환을 같이 쓴다.

export interface ProfileDraft {
  name: string
  region: string
  employees: string
  revenueEok: string
  foundedYear: string
  bizNo: string
  stockCode: string
  indutyCode: string
}

export interface CertDraft {
  iatf16949: boolean
  iso14001: boolean
}

export interface CapabilityRow {
  process: string
  material: string
  monthlyCapacity: string
}

export interface QualityDraft {
  defectPpm: string
  onTimeRatePct: string
  auditGrade: string
  yearsDealing: string
}

export interface QuoteRow {
  process: string
  material: string
  unitPrice: string
  quotedAt: string
}

export interface SupplierRegisterForm {
  source: SupplierSource
  corpCode: string
  profile: ProfileDraft
  certs: CertDraft
  capabilities: CapabilityRow[]
  quality: QualityDraft
  quotes: QuoteRow[]
}

export type FieldErrors = Record<string, string>
/** 입력칸 키로 보여 줄 오류를 찾는다. 저장을 누르기 전에는 undefined */
export type FieldErrorOf = (key: string) => string | undefined

const INT_MAX = 2_147_483_647

export function emptyProfile(): ProfileDraft {
  return { name: '', region: '', employees: '', revenueEok: '', foundedYear: '', bizNo: '', stockCode: '', indutyCode: '' }
}
export function emptyCapability(): CapabilityRow {
  return { process: '', material: '', monthlyCapacity: '' }
}
export function emptyQuality(): QualityDraft {
  return { defectPpm: '', onTimeRatePct: '', auditGrade: '', yearsDealing: '' }
}
export function emptyQuote(): QuoteRow {
  return { process: '', material: '', unitPrice: '', quotedAt: '' }
}

export function emptyRegisterForm(): SupplierRegisterForm {
  return {
    source: 'MANUAL',
    corpCode: '',
    profile: emptyProfile(),
    certs: { iatf16949: false, iso14001: false },
    capabilities: [emptyCapability()],
    quality: emptyQuality(),
    quotes: [],
  }
}

function int(raw: string): number | null {
  const s = raw.replace(/[,\s]/g, '')
  return /^-?\d+$/.test(s) && Number.isSafeInteger(Number(s)) ? Number(s) : null
}
function optionalInt(raw: string): number | null {
  return raw.trim() ? int(raw) : null
}
function required<T>(value: T | null): T {
  if (value === null) throw new Error('검사를 통과한 값만 변환한다')
  return value
}
function inRange(value: number | null, min: number, max: number): boolean {
  return value !== null && value >= min && value <= max
}

/** "97.5" → 0.975. 백분율은 소수 첫째 자리까지만 받는다 */
function ratioFromPct(raw: string): number | null {
  const s = raw.trim()
  if (!/^\d{1,3}(\.\d)?$/.test(s) || Number(s) > 100) return null
  return Math.round(Number(s) * 10) / 1000
}
function pctFromRatio(ratio: number): string {
  return String(Math.round(ratio * 1000) / 10)
}

/** 사업자등록번호는 뒤 5자리를 가려서 보낸다. 원문은 서버에 보내지 않는다 */
export function maskBizNo(raw: string): string | null {
  const digits = raw.replace(/[^\d*]/g, '')
  const m = /^(\d{3})(\d{2})(\d{5}|\*{5})$/.exec(digits)
  return m ? `${m[1]}-${m[2]}-*****` : null
}

// ── 영역별 검사. 오류는 e에 키별로 쌓는다 ──

export function validateProfile(p: ProfileDraft, thisYear: number, e: FieldErrors) {
  const name = p.name.trim()
  if (!name) e.name = '협력사명을 입력해 주세요.'
  else if (name.length > 100) e.name = '100자 이내로 입력해 주세요.'
  if (!REGIONS.includes(p.region)) e.region = '지역을 골라 주세요.'
  if (p.employees.trim() && !inRange(int(p.employees), 1, INT_MAX)) e.employees = '1 이상의 정수를 입력해 주세요.'
  if (p.revenueEok.trim() && !inRange(int(p.revenueEok), 0, INT_MAX)) e.revenueEok = '억 원 단위 정수로 입력해 주세요.'
  if (!inRange(int(p.foundedYear), 1900, thisYear)) e.foundedYear = `1900~${thisYear} 사이 연도를 입력해 주세요.`
  if (!maskBizNo(p.bizNo)) e.bizNo = '사업자등록번호 10자리를 입력해 주세요. (예: 123-45-67890)'
  if (p.stockCode.trim() && !/^\d{6}$/.test(p.stockCode.trim())) e.stockCode = '종목코드는 6자리 숫자입니다.'
  if (p.indutyCode.trim() && !/^\d{3,5}$/.test(p.indutyCode.trim())) e.indutyCode = '업종 코드는 3~5자리 숫자입니다. (예: 30332)'
}

export function validateCapabilities(rows: CapabilityRow[], e: FieldErrors) {
  if (!rows.length) e.capabilities = '생산 공정을 하나 이상 넣어 주세요.'
  else if (rows.length > 100) e.capabilities = '생산 공정은 100줄까지 넣을 수 있습니다.'
  const seen = new Set<string>()
  rows.forEach((c, i) => {
    if (!(PROCESSES as readonly string[]).includes(c.process)) e[`cap.${i}.process`] = '공정을 골라 주세요.'
    const material = c.material.trim()
    if (!material) e[`cap.${i}.material`] = '소재를 입력해 주세요.'
    else if (material.length > 100) e[`cap.${i}.material`] = '100자 이내'
    if (!inRange(int(c.monthlyCapacity), 1, 10_000_000)) e[`cap.${i}.monthlyCapacity`] = '1~10,000,000 정수'
    const key = `${c.process}/${material}`
    if (c.process && material && seen.has(key)) e[`cap.${i}.material`] = '같은 공정·소재가 이미 있습니다.'
    seen.add(key)
  })
}

export function isQualityBlank(q: QualityDraft): boolean {
  return !q.defectPpm.trim() && !q.onTimeRatePct.trim() && !q.auditGrade && !q.yearsDealing.trim()
}

/** optional이면 네 칸이 모두 비었을 때 품질 없이 보낸다. 하나라도 채우면 네 칸 모두 필요하다 */
export function validateQuality(q: QualityDraft, e: FieldErrors, optional = false) {
  if (optional && isQualityBlank(q)) return
  if (!inRange(int(q.defectPpm), 0, INT_MAX)) e.defectPpm = '0 이상의 정수를 입력해 주세요.'
  if (ratioFromPct(q.onTimeRatePct) === null) e.onTimeRatePct = '0~100 사이, 소수 첫째 자리까지 입력해 주세요.'
  if (!(AUDIT_GRADES as readonly string[]).includes(q.auditGrade)) e.auditGrade = '감사 등급을 골라 주세요.'
  if (!inRange(int(q.yearsDealing), 0, 1000)) e.yearsDealing = '0~1000 정수 (신규 후보는 0)'
}

/** today는 YYYY-MM-DD. 서버는 오늘 이후 견적일을 받지 않는다 */
export function validateQuotes(rows: QuoteRow[], today: string, e: FieldErrors) {
  if (rows.length > 100) e.quotes = '견적은 한 번에 100건까지 넣을 수 있습니다.'
  rows.forEach((q, i) => {
    if (!(PROCESSES as readonly string[]).includes(q.process)) e[`quote.${i}.process`] = '공정'
    const material = q.material.trim()
    if (!material || material.length > 100) e[`quote.${i}.material`] = '소재'
    if (!inRange(int(q.unitPrice), 1, INT_MAX)) e[`quote.${i}.unitPrice`] = '1 이상 정수(원)'
    if (!/^\d{4}-\d{2}-\d{2}$/.test(q.quotedAt)) e[`quote.${i}.quotedAt`] = '날짜'
    else if (q.quotedAt > today) e[`quote.${i}.quotedAt`] = '오늘 이후 불가'
  })
}

export function validateRegister(form: SupplierRegisterForm, thisYear: number, today: string): FieldErrors {
  const e: FieldErrors = {}
  if (form.source === 'DART' && !/^\d{8}$/.test(form.corpCode.trim())) e.corpCode = 'DART 고유번호 8자리를 입력해 주세요.'
  validateProfile(form.profile, thisYear, e)
  validateCapabilities(form.capabilities, e)
  validateQuality(form.quality, e, true)
  validateQuotes(form.quotes, today, e)
  return e
}

// ── API 요청으로 변환. 검사를 통과한 폼만 넘긴다 ──

export function profileInput(p: ProfileDraft): ProfileInput {
  return {
    name: p.name.trim(),
    region: p.region,
    employees: optionalInt(p.employees),
    revenue_eok: optionalInt(p.revenueEok),
    founded_year: required(int(p.foundedYear)),
    biz_no_masked: required(maskBizNo(p.bizNo)),
    stock_code: p.stockCode.trim() || null,
    induty_code: p.indutyCode.trim() || null,
  }
}

export function capabilitiesInput(rows: CapabilityRow[]): CapabilityInput[] {
  return rows.map((c) => ({ process: c.process, material: c.material.trim(), monthly_capacity: required(int(c.monthlyCapacity)) }))
}

export function qualityInput(q: QualityDraft): QualityInput | null {
  if (isQualityBlank(q)) return null
  return {
    defect_ppm: required(int(q.defectPpm)),
    on_time_rate: required(ratioFromPct(q.onTimeRatePct)),
    audit_grade: q.auditGrade,
    years_dealing: required(int(q.yearsDealing)),
  }
}

export function quotesInput(rows: QuoteRow[]): QuoteInput[] {
  return rows.map((q) => ({ process: q.process, material: q.material.trim(), unit_price: required(int(q.unitPrice)), quoted_at: q.quotedAt }))
}

export function createRequest(form: SupplierRegisterForm): CreateSupplierRequest {
  return {
    profile: { ...profileInput(form.profile), source: form.source, corp_code: form.source === 'DART' ? form.corpCode.trim() : null },
    certifications: { ...form.certs },
    capabilities: capabilitiesInput(form.capabilities),
    quality: qualityInput(form.quality),
  }
}

// ── 상세 응답 → 수정 폼 ──

export function profileDraft(s: AdminSupplierProfile): ProfileDraft {
  const text = (v: number | null) => (v === null ? '' : String(v))
  return {
    name: s.name,
    region: s.region,
    employees: text(s.employees),
    revenueEok: text(s.revenue_eok),
    foundedYear: String(s.founded_year),
    bizNo: s.biz_no_masked,
    stockCode: s.stock_code ?? '',
    indutyCode: s.induty_code ?? '',
  }
}

export function capabilityRows(detail: AdminSupplierDetail): CapabilityRow[] {
  return detail.capabilities.map((c) => ({ process: c.process, material: c.material, monthlyCapacity: String(c.monthly_capacity) }))
}

export function qualityDraft(q: QualityInput | null): QualityDraft {
  if (!q) return emptyQuality()
  return { defectPpm: String(q.defect_ppm), onTimeRatePct: pctFromRatio(q.on_time_rate), auditGrade: q.audit_grade, yearsDealing: String(q.years_dealing) }
}

/** PATCH에는 바뀐 필드만 보낸다. MOCK 기업은 기본 정보를 바꾸면 출처가 MANUAL로 바뀌기 때문이다 */
export function changedProfile(draft: ProfileDraft, s: AdminSupplierProfile): Partial<ProfileInput> {
  const next = profileInput(draft)
  const changes: Partial<ProfileInput> = {}
  for (const key of Object.keys(next) as (keyof ProfileInput)[]) {
    if (next[key] !== s[key]) Object.assign(changes, { [key]: next[key] })
  }
  return changes
}
