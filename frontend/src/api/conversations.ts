import { ApiError, DEFAULT_TIMEOUT_MS, request } from './client'
import { parseConversationList, parseGenerationResponse, parseMessagePage, parseSavedResponse, parseSnapshot } from './conversation-contract'
import type { ChatContext } from './types'

// Python 300초와 Spring 310초 제한보다 길게 유지한다. 조회는 짧은 DB 트랜잭션만 기다린다.
const GENERATION_TIMEOUT_MS = Math.max(330_000, DEFAULT_TIMEOUT_MS)
const ROOT = '/api/v2/conversations'
const path = (id: string) => `${ROOT}/${encodeURIComponent(id)}`
function validated<T>(value: unknown, parse: (input: unknown) => T): T {
  try { return parse(value) } catch { throw new ApiError(502, 'INVALID_RESPONSE', 'Invalid conversation response.', false) }
}
export async function createConversation(supplierId: string | null = null, signal?: AbortSignal) {
  const body = await request<unknown>(ROOT, { method: 'POST', body: JSON.stringify({ context: { selected_supplier_ids: supplierId ? [supplierId] : [] } }), signal })
  return validated(body, parseSnapshot)
}
export async function getConversation(id: string, signal?: AbortSignal) {
  return validated(await request(path(id), { signal }), (body) => parseSnapshot(body, id))
}
export async function listConversations(cursor?: string | null, signal?: AbortSignal) {
  const params = new URLSearchParams({ size: '20' }); if (cursor) params.set('cursor', cursor)
  return validated(await request(`${ROOT}?${params}`, { signal }), parseConversationList)
}
export async function renameConversation(id: string, title: string) {
  return validated(await request(path(id), { method: 'PATCH', body: JSON.stringify({ title }) }), (body) => parseSnapshot(body, id))
}
export function deleteConversation(id: string) { return request<void>(path(id), { method: 'DELETE' }) }
export async function getMessages(id: string, before: string, signal?: AbortSignal) {
  const params = new URLSearchParams({ before_seq: before, size: '50' })
  return validated(await request(`${path(id)}/messages?${params}`, { signal }), (body) => parseMessagePage(body, id))
}
export async function getSavedResponse(id: string, responseId: string, signal?: AbortSignal) {
  return validated(await request(`${path(id)}/responses/${encodeURIComponent(responseId)}`, { signal }), (body) => parseSavedResponse(body, id, responseId))
}
export async function sendMessage(id: string, query: string, context?: ChatContext, key: string = crypto.randomUUID(), signal?: AbortSignal) {
  const body = await request(`${path(id)}/messages`, {
    method: 'POST', body: JSON.stringify(context ? { query, context } : { query }), headers: { 'Idempotency-Key': key }, signal,
  }, GENERATION_TIMEOUT_MS)
  return validated(body, (value) => parseGenerationResponse(value, id))
}
