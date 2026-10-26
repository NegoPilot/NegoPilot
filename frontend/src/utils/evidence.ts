import type { Capability, Evidence, Financial, GenerationResponse, Quality, ToolCall } from '@/api/types'

/** 단일 인용과 묶음 인용을 각각의 번호로 나눈다. 저장된 답변도 같은 방식으로 표시한다. */
export type TextPart = { type: 'text'; value: string } | { type: 'cite'; n: number }

export function splitCitations(text: string): TextPart[] {
  const parts: TextPart[] = []
  const pattern = /\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]/g
  let last = 0
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0
    if (index > last) parts.push({ type: 'text', value: text.slice(last, index) })
    for (const [offset, number] of match[1]!.split(/\s*,\s*/).entries()) {
      if (offset > 0) parts.push({ type: 'text', value: ' ' })
      parts.push({ type: 'cite', n: Number(number) })
    }
    last = index + match[0].length
  }
  if (last < text.length) parts.push({ type: 'text', value: text.slice(last) })
  return parts
}

type RecordValue = Record<string, unknown>
export interface SupplierEvidence {
  supplier_id: string
  name?: string
  region?: string
  iatf16949: boolean | null
  iso14001: boolean | null
  stub: boolean
  source: string
  capabilities: Capability[]
  quality: Quality
  financials: (Financial & { statement_type: string | null })[]
  recent_quotes: { process: string; material: string; unit_price_krw: number | null; quoted_at: string; source: string }[]
  existing_contract: { payment_days: number | null; price_linkage: boolean | null } | null
  provenance: {
    profile: string
    profile_origin: string
    certifications: string
    capabilities: string
    quality: string
    financials: string
    quotes: string
    contracts: string
  }
}
function record(value: unknown): value is RecordValue {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
function boolean(value: unknown): boolean | null { return typeof value === 'boolean' ? value : null }
function source(value: unknown): string {
  return typeof value === 'string' && ['DART', 'MOCK', 'MANUAL', 'STUB', 'MIXED', 'UNKNOWN'].includes(value.toUpperCase()) ? value.toUpperCase() : 'UNKNOWN'
}
function aggregateSources(values: string[]): string {
  const unique = new Set(values)
  return unique.size === 0 ? 'UNKNOWN' : unique.size === 1 ? values[0]! : 'MIXED'
}
function own(value: RecordValue, key: string) { return Object.prototype.hasOwnProperty.call(value, key) }
function percent(value: unknown, scale: number): number | null {
  const parsed = number(value)
  return parsed !== null && parsed >= 0 && parsed * scale <= 100 ? parsed * scale : null
}
function sourceGroups(value: RecordValue) {
  const provenance = record(value.provenance) ? value.provenance : {}
  const group = (key: string, named: string) => source(own(value, key) ? value[key] : provenance[named] ?? provenance.operations)
  return {
    profile: source(provenance.profile ?? value.source),
    profile_origin: source(value.profile_origin_source ?? provenance.profile_origin),
    certifications: group('certification_source', 'certifications'),
    capabilities: group('capability_source', 'capabilities'),
    quality: group('quality_source', 'quality'),
    quotes: group('quote_source', 'quotes'),
    contracts: group('contract_source', 'contracts'),
  }
}

/** MCP의 flat 필드와 기존 정규화 응답을 표시용 형식으로 변환한다. 원본 근거는 변경하지 않는다. */
export function parseSupplierEvidence(evidence: Evidence): SupplierEvidence | null {
  if (evidence.source !== 'business' || evidence.kind !== 'supplier') return null
  try {
    const value: unknown = JSON.parse(evidence.text)
    if (!record(value) || typeof value.supplier_id !== 'string') return null
    const groups = sourceGroups(value)
    const quality = record(value.quality) ? value.quality : value
    const financials: SupplierEvidence['financials'] = []
    for (const row of Array.isArray(value.financials) ? value.financials : []) {
      if (!record(row)) continue
      const canonical = own(row, 'year')
      const year = number(canonical ? row.year : row.bsns_year)
      if (year === null || !Number.isInteger(year)) continue
      const liabilities = number(row.total_liabilities), equity = number(row.total_equity)
      const ratio = liabilities !== null && equity !== null && equity !== 0 ? number(liabilities / equity * 100) : null
      financials.push({
        year,
        revenue_krw: number(canonical ? row.revenue_krw : row.revenue),
        operating_profit_krw: number(canonical ? row.operating_profit_krw : row.operating_income),
        debt_ratio: canonical ? number(row.debt_ratio) : ratio,
        source: source(row.source),
        statement_type: typeof row.fs_div === 'string' ? row.fs_div : null,
      })
    }
    const quotes: SupplierEvidence['recent_quotes'] = []
    for (const row of Array.isArray(value.recent_quotes) ? value.recent_quotes : []) {
      if (!record(row) || typeof row.process !== 'string' || typeof row.material !== 'string' || typeof row.quoted_at !== 'string') continue
      quotes.push({ process: row.process, material: row.material, quoted_at: row.quoted_at,
        unit_price_krw: number(own(row, 'unit_price_krw') ? row.unit_price_krw : row.unit_price),
        source: source(own(row, 'source') ? row.source : groups.quotes) })
    }
    const capabilities: Capability[] = []
    for (const row of Array.isArray(value.capabilities) ? value.capabilities : []) {
      if (!record(row) || typeof row.process !== 'string' || typeof row.material !== 'string') continue
      const capacity = number(row.monthly_capacity)
      if (capacity !== null && capacity >= 0) capabilities.push({ process: row.process, material: row.material, monthly_capacity: capacity })
    }
    const contract = own(value, 'existing_contract') ? value.existing_contract : value
    const payment = record(contract) ? number(contract.payment_days) : null
    const linkage = record(contract) ? boolean(contract.price_linkage) : null
    return {
      supplier_id: value.supplier_id,
      ...(typeof value.name === 'string' ? { name: value.name } : {}),
      ...(typeof value.region === 'string' ? { region: value.region } : {}),
      iatf16949: boolean(value.iatf16949), iso14001: boolean(value.iso14001),
      stub: value.stub === true || evidence.meta.stub === true,
      source: source(value.source), capabilities,
      quality: {
        defect_ppm: number(quality.defect_ppm),
        on_time_delivery_rate: own(quality, 'on_time_delivery_rate') ? percent(quality.on_time_delivery_rate, 1) : percent(quality.on_time_rate, 100),
      },
      financials, recent_quotes: quotes,
      existing_contract: payment === null && linkage === null ? null : { payment_days: payment, price_linkage: linkage },
      provenance: { ...groups, financials: aggregateSources(financials.map((row) => row.source)) },
    }
  } catch { return null }
}

const KIND_LABELS: Record<string, string> = {
  supplier: '협력사 상세 조회',
  ranked_supplier: '협력사 순위 계산',
  offer_finding: '협상안 법규 검사',
  offer_verdict: '협상안 법규 검사',
  article: '법령 조문 조회',
  regulation_hit: '법령 검색',
  supplier_hit: '협력사 검색',
}

export function evidenceKindLabel(evidence: Evidence): string {
  return KIND_LABELS[evidence.kind] ?? (evidence.source === 'law' ? '법령 자료' : '업무 데이터 조회')
}

/** 근거가 나온 도구 호출. 하위 질문의 호출 중 첫 번째, 없으면 미리 조회한 조문 */
export function findToolCall(response: GenerationResponse, evidence: Evidence): ToolCall | null {
  const outcome = response.routing.outcomes.find((o) => o.id === evidence.sub_query)
  if (!outcome) return null
  const articleNo = evidence.meta.article_no
  if (evidence.source === 'law' && typeof articleNo === 'string') {
    const byArticle = [...outcome.calls, ...outcome.referenced_articles].find((c) => c.args.article_no === articleNo)
    if (byArticle) return byArticle
  }
  return outcome.calls[0] ?? outcome.referenced_articles[0] ?? null
}

export function toolSignature(call: ToolCall): string {
  return `${call.server}.${call.tool}(${Object.keys(call.args).join(', ')})`
}

/** 테스트 데이터로 만든 응답인지. stub 모드이거나 도구 결과에 stub 표시가 있으면 true */
export function isStubResponse(response: GenerationResponse, evidence?: Evidence | null): boolean {
  if (response.mode === 'stub') return true
  if (evidence?.meta.stub === true) return true
  return false
}

/** 인용 원문 링크는 HTTP(S)만 연다 (direct-chat 계약) */
export function isHttpUrl(url: string | null | undefined): url is string {
  if (!url) return false
  try {
    const { protocol } = new URL(url)
    return protocol === 'http:' || protocol === 'https:'
  } catch {
    return false
  }
}

const FIELD_LABELS: Record<string, string> = {
  supplier_id: '협력사 ID',
  name: '협력사명',
  region: '지역',
  iatf16949: 'IATF 16949',
  iso14001: 'ISO 14001',
  process: '공정',
  material: '소재',
  monthly_capacity: '월 생산능력',
  defect_ppm: '불량률(ppm)',
  on_time_rate: '납기 준수율',
  source: '기본 정보 출처',
  profile_origin_source: '기본 정보 원출처',
  certification_source: '인증 출처',
  capability_source: '생산능력 출처',
  quality_source: '품질과 납기 출처',
  debt_ratio: '부채비율',
  operating_margin: '영업이익률',
  raw_material_ratio: '원재료 비중',
  revenue_eok: '매출액(억 원)',
  rfq_id: 'RFQ',
  item: '품목',
  quantity: '수량',
  target_price: '목표 단가',
  oem_id: '완성차',
  vehicle: '차종',
  part_name: '품목',
  annual_volume: '연간 물량',
  required_cert: '필수 인증',
  due_date: '납기',
  weights: '가중치',
  needed_monthly_capacity: '필요 월 생산능력',
  candidate_count: '후보 수',
  excluded: '제외 협력사',
  rank: '순위',
  total_score: '종합 점수',
  scores: '항목별 점수',
  last_price: '최근 견적 단가',
  gap_to_target_pct: '목표 단가 대비',
  audit_grade: '감사 등급',
  years_dealing: '거래 연수',
  payment_days: '대금 지급 기한(일)',
  price_linkage: '납품대금 연동',
  notes: '참고 사항',
  flags: '법규 점검',
}

const SCORE_LABELS: Record<string, string> = { price: '가격', quality: '품질', delivery: '납기' }

/** 중첩 값(가중치, 제외 목록, flags 등)을 JSON 대신 읽을 수 있는 문장으로. 여러 항목은 줄을 나눈다 */
function nestedDisplay(key: string, item: object): string {
  if (Array.isArray(item)) {
    return item.map((row) => {
      if (!record(row)) return String(row)
      if (key === 'excluded') return `${row.name ?? row.supplier_id}(${row.supplier_id}): ${row.reason ?? '사유 없음'}`
      if (key === 'flags') return `${row.message ?? row.code}${row.law_refs ? ` (${row.law_refs})` : ''}`
      return nestedDisplay(key, row)
    }).join('\n') || '없음'
  }
  return Object.entries(item).map(([k, v]) => {
    const label = SCORE_LABELS[k] ?? FIELD_LABELS[k] ?? k
    if (key === 'weights' && number(v) !== null) return `${label} ${(Number(v) * 100).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%`
    if (typeof v === 'number') return `${label} ${v.toLocaleString('ko-KR')}`
    return `${label} ${v === null || v === undefined ? '정보 없음' : typeof v === 'object' ? nestedDisplay(k, v) : String(v)}`
  }).join(' · ')
}

/**
 * 상세 조회(supplier)가 아닌 업무 근거(예: 검색 결과 supplier_hit)는 도구 결과 한 행이 JSON으로 들어 있다.
 * 키·값 표로 보여 준다. 길이 제한으로 잘렸거나 JSON이 아니면 null (원문 그대로 표시)
 */
export function parseEvidenceFields(evidence: Evidence): { key: string; label: string; value: string; source: string }[] | null {
  if (evidence.source !== 'business' || evidence.kind === 'supplier') return null
  let value: unknown
  try { value = JSON.parse(evidence.text) } catch { return null }
  if (!record(value)) return null
  const groups = sourceGroups(value)
  function fieldSource(key: string): string {
    if (['supplier_id', 'name', 'region', 'employees', 'revenue_eok', 'founded_year', 'biz_no_masked', 'corp_code', 'stock_code', 'induty_code'].includes(key)) return groups.profile
    if (['iatf16949', 'iso14001'].includes(key)) return groups.certifications
    if (['process', 'material', 'monthly_capacity', 'capabilities'].includes(key)) return groups.capabilities
    if (['defect_ppm', 'on_time_rate', 'audit_grade', 'years_dealing'].includes(key)) return groups.quality
    if (['unit_price', 'last_price', 'quoted_at', 'recent_quotes'].includes(key)) return groups.quotes
    if (['payment_days', 'price_linkage', 'raw_material_ratio', 'auto_renewal'].includes(key)) return groups.contracts
    return 'UNKNOWN'
  }
  function row(key: string, item: unknown, source: string) {
    let display: string
    if (item === null || item === undefined) display = '정보 없음'
    else if (['on_time_rate', 'debt_ratio', 'operating_margin', 'raw_material_ratio'].includes(key) && number(item) !== null) {
      display = `${(Number(item) * 100).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%`
    } else if (key === 'gap_to_target_pct' && number(item) !== null) {
      display = `${Number(item) > 0 ? '+' : ''}${Number(item).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%`
    } else if (typeof item === 'boolean') display = item ? '예' : '아니오'
    else if (typeof item === 'number') display = item.toLocaleString('ko-KR')
    else if (typeof item === 'object') display = nestedDisplay(key, item)
    else display = String(item)
    return { key, label: FIELD_LABELS[key] ?? key, value: display, source }
  }
  return Object.entries(value).filter(([key]) => key !== 'stub').flatMap(([key, item]) =>
    // 순위 계산 조건의 RFQ는 한 칸에 몰지 않고 항목별 행으로 펼친다. 출처 필드가 없어 출처는 정보 없음
    key === 'rfq' && record(item)
      ? Object.entries(item).map(([k, v]) => row(k, v, 'UNKNOWN'))
      : [row(key, item, fieldSource(key))])
}
