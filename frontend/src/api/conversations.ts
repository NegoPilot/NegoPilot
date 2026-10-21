import { request } from './client'
import type { ChatContext, ConversationSnapshot, GenerationResponse } from './types'

// 생성 답변 대화 API (agent-generation 계약 v0.3). 대화는 Spring 메모리에만 저장된다.
// 실제 모델은 수십 초 걸릴 수 있어 화면 쪽 제한 시간을 넉넉히 둔다 (먼저 끊기는 쪽은 Spring 설정)
const GENERATION_TIMEOUT_MS = 200_000

/** 선택 기업(0..1개)을 문맥으로 새 대화를 만든다. 기업 선택 없이도 만들 수 있다 (direct-chat 계약) */
export function createConversation(supplierId: string | null): Promise<ConversationSnapshot> {
  return request<ConversationSnapshot>('/api/v2/conversations', {
    method: 'POST',
    body: JSON.stringify({ context: { selected_supplier_ids: supplierId ? [supplierId] : [] } }),
  })
}

export function getConversation(conversationId: string): Promise<ConversationSnapshot> {
  return request<ConversationSnapshot>(`/api/v2/conversations/${encodeURIComponent(conversationId)}`)
}

/** context를 넣으면 이 요청부터 선택 문맥이 바뀐다. 생략하면 Spring에 저장된 문맥을 쓴다 */
export function sendMessage(conversationId: string, query: string, context?: ChatContext): Promise<GenerationResponse> {
  return request<GenerationResponse>(
    `/api/v2/conversations/${encodeURIComponent(conversationId)}/messages`,
    { method: 'POST', body: JSON.stringify(context ? { query, context } : { query }) },
    GENERATION_TIMEOUT_MS,
  )
}
