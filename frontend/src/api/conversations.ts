import { request } from './client'
import type { ChatResponse, ConversationSnapshot } from './types'

/** 기업 하나를 문맥으로 새 대화를 만든다. 기업을 바꾸면 새 대화를 만드는 것이 기본 동작이다 */
export function createConversation(supplierId: string): Promise<ConversationSnapshot> {
  return request<ConversationSnapshot>('/api/v1/conversations', {
    method: 'POST',
    body: JSON.stringify({ context: { selected_supplier_ids: [supplierId] } }),
  })
}

export function getConversation(conversationId: string): Promise<ConversationSnapshot> {
  return request<ConversationSnapshot>(`/api/v1/conversations/${encodeURIComponent(conversationId)}`)
}

/** context를 생략하면 Spring에 저장된 문맥을 쓴다 */
export function sendMessage(conversationId: string, query: string): Promise<ChatResponse> {
  return request<ChatResponse>(`/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: 'POST',
    body: JSON.stringify({ query }),
  })
}
