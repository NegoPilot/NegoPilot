import { request } from './client'
import type { ConversationSnapshot, GenerationResponse } from './types'

// 생성 답변 대화 API (agent-generation 계약 v0.3). 대화는 Spring 메모리에만 저장된다.
// 실제 모델은 수십 초 걸릴 수 있어 화면 쪽 제한 시간을 넉넉히 둔다 (먼저 끊기는 쪽은 Spring 설정)
const GENERATION_TIMEOUT_MS = 200_000

/** 새 대화. 화면은 협력사 선택을 문맥으로 보내지 않는다 (#64). 계약상 context는 필수라 빈 선택을 보낸다 */
export function createConversation(): Promise<ConversationSnapshot> {
  return request<ConversationSnapshot>('/api/v2/conversations', {
    method: 'POST',
    body: JSON.stringify({ context: { selected_supplier_ids: [] } }),
  })
}

export function getConversation(conversationId: string): Promise<ConversationSnapshot> {
  return request<ConversationSnapshot>(`/api/v2/conversations/${encodeURIComponent(conversationId)}`)
}

/** 질문만 보낸다. Spring이 저장된 문맥(빈 선택)과 최근 이력으로 답한다 */
export function sendMessage(conversationId: string, query: string): Promise<GenerationResponse> {
  return request<GenerationResponse>(
    `/api/v2/conversations/${encodeURIComponent(conversationId)}/messages`,
    { method: 'POST', body: JSON.stringify({ query }) },
    GENERATION_TIMEOUT_MS,
  )
}
