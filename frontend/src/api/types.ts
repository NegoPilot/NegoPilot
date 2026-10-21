// Spring 공개 API의 응답 형식. 계약이 바뀌면 이 파일을 같은 PR에서 고친다.
// - 협력사 조회: docs/contracts/supplier-chat.md (v0.2, /api/v1)
// - 생성 답변 대화: docs/contracts/agent-generation.md (v0.3, /api/v2)

export interface Envelope<T> {
  schema_version: '0.2'
  mode: 'stub'
  data: T
}

export interface CatalogProcess {
  process: string
  materials: string[]
}

export interface Catalog {
  processes: CatalogProcess[]
  regions: string[]
}

export interface Capability {
  process: string
  material: string
  monthly_capacity: number
}

export interface Provenance {
  profile: string
  financials: string
  operations: string
}

export interface SupplierSummary {
  supplier_id: string
  name: string
  region: string
  iatf16949: boolean
  matched_capabilities: Capability[]
  provenance: Provenance
}

export interface SupplierList {
  items: SupplierSummary[]
  returned_count: number
  limit: number
}

export interface Quality {
  defect_ppm: number | null
  // 0..100 백분율. 화면에서 100을 곱하지 않는다
  on_time_delivery_rate: number | null
}

export interface Financial {
  year: number
  revenue_krw: number | null
  operating_profit_krw: number | null
  // 이미 백분율로 환산한 값. 100을 넘을 수 있다
  debt_ratio: number | null
  source: string
}

export interface Quote {
  process: string
  material: string
  unit_price_krw: number
  quoted_at: string
}

export interface ExistingContract {
  payment_days: number
  price_linkage: boolean
}

export interface SupplierDetail {
  supplier_id: string
  name: string
  region: string
  iatf16949: boolean
  // 계약(v0.2)에는 아직 없다. live get_supplier 근거(DB supplier_overview)에는 들어 있다
  iso14001?: boolean
  capabilities: Capability[]
  quality: Quality
  financials: Financial[]
  recent_quotes: Quote[]
  existing_contract: ExistingContract | null
  provenance: Provenance
}

/** 검색 조건. 빈 선택값은 보내지 않는다 */
export interface SupplierSearchParams {
  process: string
  material?: string
  region?: string
  iatf_required?: boolean
  // 아래 두 조건은 계약 변경(#43, #53) 전까지 Spring이 400으로 거절한다
  iso14001_required?: boolean
  min_monthly_capacity?: number
  limit?: number
}

// ── 생성 답변 대화 (docs/contracts/agent-generation.md v0.3, /api/v2) ──

export interface ChatContext {
  /** 0..1개. 대명사를 해석하는 보조 데이터일 뿐 기업 사실은 도구 결과로 확인한다 */
  selected_supplier_ids: string[]
}

export interface Citation {
  /** 본문의 [1], [2] 번호 */
  n: number
  evidence_id: string
  label: string
  source_url: string | null
}

export interface Evidence {
  id: string
  sub_query: string
  source: 'law' | 'business'
  /** article, regulation_hit, supplier, offer_finding, ranked_supplier ... */
  kind: string
  label: string
  /** 근거 본문. business 근거는 도구 결과 JSON 문자열일 수 있다 */
  text: string
  citable: boolean
  source_url: string | null
  meta: Record<string, unknown>
}

export type AnswerKind = 'answer' | 'clarify' | 'refusal' | 'no_result' | 'failed'

export interface Answer {
  kind: AnswerKind
  text: string
  citations: Citation[]
  options: string[]
  /** false면 화면에 "근거 자료 없음" */
  grounded: boolean
  notices: string[]
  evidence: Evidence[]
  verification: unknown
}

export interface ToolCall {
  server: string
  tool: string
  args: Record<string, unknown>
  result: unknown
  error: string | null
}

export interface RouteOutcome {
  id: string
  intent: string
  status: string
  calls: ToolCall[]
  referenced_articles: ToolCall[]
  missing: string[]
  message: string | null
}

export interface RouteResult {
  schema_version: string
  status: 'routed' | 'not_routed'
  halted: boolean
  halt_reason: string | null
  outcomes: RouteOutcome[]
}

export interface GenerationResponse {
  schema_version: '0.3'
  request_id: string
  mode: 'live' | 'stub'
  execution_status: 'COMPLETED' | 'NEEDS_INPUT' | 'FAILED'
  generation_status: 'COMPLETED' | 'FAILED'
  context: ChatContext
  decomposition: unknown
  routing: RouteResult
  answer: Answer
  error: { code: string; message: string; retryable: boolean } | null
  conversation_id: string
  response_id: string
}

export interface StoredMessage {
  message_id: string
  role: 'user' | 'assistant'
  content: string
  response: GenerationResponse | null
}

export interface ConversationSnapshot {
  schema_version: '0.3'
  conversation_id: string
  context: ChatContext
  storage: 'memory'
  messages: StoredMessage[]
}

export interface ApiErrorBody {
  request_id: string | null
  error: {
    code: string
    message: string
    retryable: boolean
  }
}
