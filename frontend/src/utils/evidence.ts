import type { Evidence, GenerationResponse, SupplierDetail, ToolCall } from '@/api/types'

/** 본문의 [1] 같은 인용 번호를 나눈다. 화면에서 번호를 근거 링크로 바꿀 때 쓴다 */
export type TextPart = { type: 'text'; value: string } | { type: 'cite'; n: number }

export function splitCitations(text: string): TextPart[] {
  const parts: TextPart[] = []
  const pattern = /\[(\d+)\]/g
  let last = 0
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0
    if (index > last) parts.push({ type: 'text', value: text.slice(last, index) })
    parts.push({ type: 'cite', n: Number(match[1]) })
    last = index + match[0].length
  }
  if (last < text.length) parts.push({ type: 'text', value: text.slice(last) })
  return parts
}

/** business-mcp get_supplier 근거는 도구 결과 JSON이 text에 들어 있다. 형식이 다르면 null */
export function parseSupplierEvidence(evidence: Evidence): Partial<SupplierDetail> & { supplier_id: string; stub?: boolean; source?: string } | null {
  if (evidence.source !== 'business' || evidence.kind !== 'supplier') return null
  try {
    const value: unknown = JSON.parse(evidence.text)
    if (typeof value === 'object' && value !== null && typeof (value as { supplier_id?: unknown }).supplier_id === 'string') {
      return value as Partial<SupplierDetail> & { supplier_id: string }
    }
  } catch {
    return null
  }
  return null
}

const KIND_LABELS: Record<string, string> = {
  supplier: '협력사 상세 조회',
  ranked_supplier: '협력사 순위 계산',
  offer_finding: '협상안 법규 검사',
  offer_verdict: '협상안 법규 검사',
  article: '법령 조문 조회',
  regulation_hit: '법령 검색',
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
