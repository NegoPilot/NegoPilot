// Spring 공개 API의 응답 형식. 기준: docs/contracts/supplier-chat.md (v0.2), supplier-chat.openapi.json
// 계약이 바뀌면 이 파일을 같은 PR에서 고친다.

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
  // 계약 변경(#43) 전까지 Spring이 400으로 거절한다
  min_monthly_capacity?: number
  limit?: number
}

export interface ChatContext {
  selected_supplier_ids: string[]
}

export interface SupplierCard {
  type: 'supplier_detail'
  supplier: SupplierDetail
  evidence_ids: string[]
}

export interface StubEvidence {
  evidence_id: string
  server: 'business-mcp'
  tool: 'get_supplier'
  supplier_id: string
  source: 'stub'
  executed: false
}

export interface Clarification {
  missing_fields: string[]
  question: string
  options: string[]
}

export interface Presentation {
  status: 'READY' | 'NEEDS_INPUT'
  renderer: 'template'
  text: string
  cards: SupplierCard[]
}

export interface ChatResponse {
  schema_version: '0.2'
  mode: 'stub'
  request_id: string
  conversation_id: string
  response_id: string
  execution_status: 'COMPLETED' | 'NEEDS_INPUT'
  generation_status: 'NOT_IMPLEMENTED'
  resolved_context: ChatContext
  presentation: Presentation
  citations: unknown[]
  evidence: StubEvidence[]
  clarification: Clarification | null
}

export interface StoredMessage {
  message_id: string
  role: 'user' | 'assistant'
  content: string
  response: ChatResponse | null
}

export interface ConversationSnapshot {
  schema_version: '0.2'
  mode: 'stub'
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
