import { AUDIT_GRADES, PROCESSES } from '@/data/dataCatalog'

// 협력사 등록 폼 ↔ business-mcp DB 행 (schema.sql).
// 입력칸은 문자열로 두고, 저장할 때 표의 열 이름과 단위로 바꾼다.
// 백분율 입력(납기 준수율, 원재료 비중)은 DB에 0..1 비율로 저장한다.

export interface CapabilityRow {
  process: string
  material: string
  monthlyCapacity: string
}

export interface QuoteRow {
  process: string
  material: string
  unitPrice: string
  quotedAt: string
}

export interface FinancialRow {
  year: string
  fsDiv: 'OFS' | 'CFS'
  revenue: string
  operatingIncome: string
  netIncome: string
  totalAssets: string
  totalLiabilities: string
  totalEquity: string
}

export interface SupplierForm {
  supplierId: string
  source: 'MOCK' | 'DART'
  corpCode: string
  stockCode: string
  indutyCode: string
  name: string
  region: string
  employees: string
  revenueEok: string
  foundedYear: string
  bizNo: string
  iatf16949: boolean
  iso14001: boolean
  capabilities: CapabilityRow[]
  defectPpm: string
  onTimeRatePct: string
  auditGrade: string
  yearsDealing: string
  hasContract: boolean
  paymentDays: string
  priceLinkage: boolean
  rawMaterialPct: string
  autoRenewal: boolean
  quotes: QuoteRow[]
  financials: FinancialRow[]
}

export function emptyCapability(): CapabilityRow {
  return { process: '', material: '', monthlyCapacity: '' }
}
export function emptyQuote(): QuoteRow {
  return { process: '', material: '', unitPrice: '', quotedAt: '' }
}
export function emptyFinancial(): FinancialRow {
  return { year: '', fsDiv: 'OFS', revenue: '', operatingIncome: '', netIncome: '', totalAssets: '', totalLiabilities: '', totalEquity: '' }
}

export function emptySupplierForm(supplierId: string): SupplierForm {
  return {
    supplierId,
    source: 'MOCK',
    corpCode: '',
    stockCode: '',
    indutyCode: '',
    name: '',
    region: '',
    employees: '',
    revenueEok: '',
    foundedYear: '',
    bizNo: '',
    iatf16949: false,
    iso14001: false,
    capabilities: [emptyCapability()],
    defectPpm: '',
    onTimeRatePct: '',
    auditGrade: '',
    yearsDealing: '0',
    hasContract: false,
    paymentDays: '',
    priceLinkage: false,
    rawMaterialPct: '',
    autoRenewal: false,
    quotes: [],
    financials: [],
  }
}

function int(raw: string): number | null {
  const s = raw.replace(/[,\s]/g, '')
  return /^-?\d+$/.test(s) ? Number(s) : null
}
function decimal(raw: string): number | null {
  const s = raw.replace(/[,\s]/g, '')
  return /^\d+(\.\d+)?$/.test(s) ? Number(s) : null
}

/** 사업자등록번호는 뒤 5자리를 가려서 저장한다 (DB 열이 biz_no_masked) */
export function maskBizNo(raw: string): string | null {
  const digits = raw.replace(/[^\d*]/g, '')
  const m = /^(\d{3})(\d{2})(\d{5}|\*{5})$/.exec(digits)
  return m ? `${m[1]}-${m[2]}-*****` : null
}

export type SupplierErrors = Record<string, string>
/** 입력칸 키로 보여 줄 오류를 찾는다. 저장을 누르기 전에는 undefined */
export type SupplierErrorOf = (key: string) => string | undefined

export function validateSupplier(form: SupplierForm, takenIds: Set<string>, thisYear: number): SupplierErrors {
  const e: SupplierErrors = {}
  const id = form.supplierId.trim()
  if (!/^SUP-\d{4}$/.test(id)) e.supplierId = 'SUP-0000 형식으로 입력해 주세요.'
  else if (takenIds.has(id)) e.supplierId = '이미 있는 협력사 ID입니다.'
  if (form.source === 'DART' && !/^\d{8}$/.test(form.corpCode.trim())) e.corpCode = 'DART 고유번호 8자리를 입력해 주세요.'
  if (form.stockCode.trim() && !/^\d{6}$/.test(form.stockCode.trim())) e.stockCode = '종목코드는 6자리 숫자입니다.'
  if (form.indutyCode.trim() && !/^\d{3,5}$/.test(form.indutyCode.trim())) e.indutyCode = '업종 코드는 3~5자리 숫자입니다. (예: 30332)'
  if (!form.name.trim()) e.name = '협력사명을 입력해 주세요.'
  if (!form.region.trim()) e.region = '지역을 골라 주세요.'
  if (form.employees.trim()) {
    const n = int(form.employees)
    if (n === null || n <= 0) e.employees = '1 이상의 정수를 입력해 주세요.'
  }
  if (form.revenueEok.trim() && int(form.revenueEok) === null) e.revenueEok = '억 원 단위 정수로 입력해 주세요.'
  const founded = int(form.foundedYear)
  if (founded === null || founded < 1900 || founded > thisYear) e.foundedYear = `1900~${thisYear} 사이 연도를 입력해 주세요.`
  if (!maskBizNo(form.bizNo)) e.bizNo = '사업자등록번호 10자리를 입력해 주세요. (예: 123-45-67890)'

  if (!form.capabilities.length) e.capabilities = '생산 공정을 하나 이상 넣어 주세요.'
  const seen = new Set<string>()
  form.capabilities.forEach((c, i) => {
    if (!(PROCESSES as readonly string[]).includes(c.process)) e[`cap.${i}.process`] = '공정을 골라 주세요.'
    if (!c.material.trim()) e[`cap.${i}.material`] = '소재를 입력해 주세요.'
    const cap = int(c.monthlyCapacity)
    if (cap === null || cap <= 0) e[`cap.${i}.monthlyCapacity`] = '1 이상의 정수'
    const key = `${c.process}/${c.material.trim()}`
    if (c.process && c.material.trim() && seen.has(key)) e[`cap.${i}.material`] = '같은 공정·소재가 이미 있습니다.'
    seen.add(key)
  })

  const ppm = int(form.defectPpm)
  if (ppm === null || ppm < 0) e.defectPpm = '0 이상의 정수를 입력해 주세요.'
  const onTime = decimal(form.onTimeRatePct)
  if (onTime === null || onTime > 100) e.onTimeRatePct = '0~100 사이 백분율을 입력해 주세요.'
  if (!(AUDIT_GRADES as readonly string[]).includes(form.auditGrade)) e.auditGrade = '감사 등급을 골라 주세요.'
  const years = int(form.yearsDealing)
  if (years === null || years < 0) e.yearsDealing = '0 이상의 정수 (신규 후보는 0)'

  if (form.hasContract) {
    const days = int(form.paymentDays)
    if (days === null || days <= 0) e.paymentDays = '1 이상의 정수를 입력해 주세요.'
    const ratio = decimal(form.rawMaterialPct)
    if (ratio === null || ratio > 100) e.rawMaterialPct = '0~100 사이 백분율을 입력해 주세요.'
  }

  form.quotes.forEach((q, i) => {
    if (!q.process) e[`quote.${i}.process`] = '공정'
    if (!q.material.trim()) e[`quote.${i}.material`] = '소재'
    const price = int(q.unitPrice)
    if (price === null || price <= 0) e[`quote.${i}.unitPrice`] = '1 이상'
    if (!/^\d{4}-\d{2}-\d{2}$/.test(q.quotedAt)) e[`quote.${i}.quotedAt`] = '날짜'
  })

  const years2 = new Set<string>()
  form.financials.forEach((f, i) => {
    const y = int(f.year)
    if (y === null || y < 1990 || y > thisYear) e[`fin.${i}.year`] = '연도'
    else if (years2.has(f.year)) e[`fin.${i}.year`] = '중복 연도'
    years2.add(f.year)
    for (const key of ['revenue', 'operatingIncome', 'netIncome', 'totalAssets', 'totalLiabilities', 'totalEquity'] as const) {
      if (f[key].trim() && int(f[key]) === null) e[`fin.${i}.${key}`] = '정수(원)'
    }
  })
  return e
}

function optionalInt(raw: string): number | null {
  return raw.trim() ? int(raw) : null
}

/** 표별로 저장할 행. 열 이름은 schema.sql 그대로다 */
export function supplierPayload(form: SupplierForm) {
  const id = form.supplierId.trim()
  const onTime = decimal(form.onTimeRatePct)
  const ratio = decimal(form.rawMaterialPct)
  return {
    suppliers: {
      supplier_id: id,
      source: form.source,
      corp_code: form.source === 'DART' ? form.corpCode.trim() : null,
      stock_code: form.stockCode.trim() || null,
      induty_code: form.indutyCode.trim() || null,
      name: form.name.trim(),
      region: form.region.trim(),
      employees: optionalInt(form.employees),
      revenue_eok: optionalInt(form.revenueEok),
      founded_year: int(form.foundedYear),
      biz_no_masked: maskBizNo(form.bizNo),
      iatf16949: form.iatf16949,
      iso14001: form.iso14001,
    },
    capabilities: form.capabilities.map((c) => ({
      supplier_id: id,
      process: c.process,
      material: c.material.trim(),
      monthly_capacity: int(c.monthlyCapacity),
    })),
    quality: {
      supplier_id: id,
      defect_ppm: int(form.defectPpm),
      on_time_rate: onTime === null ? null : Math.round(onTime * 10) / 1000,
      audit_grade: form.auditGrade || null,
      years_dealing: int(form.yearsDealing),
    },
    contracts: form.hasContract
      ? {
          supplier_id: id,
          payment_days: int(form.paymentDays),
          price_linkage: form.priceLinkage,
          raw_material_ratio: ratio === null ? null : Math.round(ratio) / 100,
          auto_renewal: form.autoRenewal,
        }
      : null,
    quotes: form.quotes.map((q) => ({
      supplier_id: id,
      process: q.process,
      material: q.material.trim(),
      unit_price: int(q.unitPrice),
      quoted_at: q.quotedAt,
    })),
    financials: form.financials.map((f) => ({
      supplier_id: id,
      bsns_year: int(f.year),
      fs_div: f.fsDiv,
      source: form.source,
      revenue: optionalInt(f.revenue),
      operating_income: optionalInt(f.operatingIncome),
      net_income: optionalInt(f.netIncome),
      total_assets: optionalInt(f.totalAssets),
      total_liabilities: optionalInt(f.totalLiabilities),
      total_equity: optionalInt(f.totalEquity),
    })),
  }
}

export type SupplierPayload = ReturnType<typeof supplierPayload>
