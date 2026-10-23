import type { ConversationList, ConversationSnapshot, GenerationResponse, MessagePage, SavedResponse } from './types'

type ObjectValue = Record<string, unknown>
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i
function check(value: unknown): asserts value { if (!value) throw new Error('Invalid conversation response') }
function object(value: unknown): asserts value is ObjectValue { check(typeof value === 'object' && value !== null && !Array.isArray(value)) }
function text(value: unknown): asserts value is string { check(typeof value === 'string') }
function uuid(value: unknown): asserts value is string { text(value); check(UUID.test(value)) }
function time(value: unknown) { text(value); check(Number.isFinite(Date.parse(value))) }
function context(value: unknown) {
  object(value)
  check(Array.isArray(value.selected_supplier_ids) && value.selected_supplier_ids.length <= 1)
  for (const id of value.selected_supplier_ids) { text(id); check(/^[A-Za-z0-9_-]{1,100}$/.test(id)) }
}
function cursor(value: unknown, sequence = false) { check(value === null || (typeof value === 'string' && (sequence ? /^[1-9][0-9]*$/.test(value) : /^[A-Za-z0-9_-]+$/.test(value)))) }
function envelope(value: unknown): asserts value is ObjectValue {
  object(value); check(value.schema_version === '0.4' && value.storage === 'database')
}
function summary(value: unknown) {
  object(value); uuid(value.conversation_id); text(value.title); check(value.title.length > 0)
  time(value.created_at); time(value.updated_at); context(value.context)
  check(Number.isSafeInteger(value.message_count) && Number(value.message_count) >= 0)
}
function strings(value: unknown) { check(Array.isArray(value) && value.every((item) => typeof item === 'string')) }

export function parseGenerationResponse(value: unknown, conversationId?: string): GenerationResponse {
  parseReviewResponse(value)
  object(value); uuid(value.conversation_id); uuid(value.response_id)
  check(!conversationId || value.conversation_id === conversationId)
  return value as unknown as GenerationResponse
}

export function parseReviewResponse(value: unknown): Omit<GenerationResponse, 'conversation_id' | 'response_id'> {
  object(value); check(value.schema_version === '0.3'); uuid(value.request_id)
  check(value.mode === 'live' || value.mode === 'stub')
  check(['COMPLETED', 'NEEDS_INPUT', 'FAILED'].includes(String(value.execution_status)))
  check(value.generation_status === (value.execution_status === 'FAILED' ? 'FAILED' : 'COMPLETED'))
  context(value.context); object(value.decomposition); object(value.routing); object(value.answer)
  const answer = value.answer
  check(['answer', 'clarify', 'refusal', 'no_result', 'failed'].includes(String(answer.kind)))
  text(answer.text); check(answer.text.trim().length > 0)
  check(typeof answer.grounded === 'boolean'); strings(answer.options); strings(answer.notices)
  check(Array.isArray(answer.citations) && Array.isArray(answer.evidence))
  check('verification' in answer)
  const evidenceIds = new Set<string>()
  for (const item of answer.evidence) {
    object(item); text(item.id); text(item.label); text(item.kind); text(item.text); text(item.sub_query)
    check(!evidenceIds.has(item.id)); evidenceIds.add(item.id)
    check(item.source === 'law' || item.source === 'business'); check(typeof item.citable === 'boolean')
    check(item.source_url === null || typeof item.source_url === 'string'); object(item.meta)
  }
  for (const item of answer.citations) {
    object(item); check(Number.isSafeInteger(item.n) && Number(item.n) > 0); text(item.evidence_id); text(item.label)
    check(evidenceIds.has(item.evidence_id)); check(item.source_url === null || typeof item.source_url === 'string')
  }
  check(Array.isArray(value.routing.outcomes))
  for (const outcome of value.routing.outcomes) {
    object(outcome); text(outcome.id); text(outcome.intent); text(outcome.status)
    strings(outcome.missing); check(outcome.message === null || typeof outcome.message === 'string')
    check(Array.isArray(outcome.calls) && Array.isArray(outcome.referenced_articles))
    for (const call of [...outcome.calls, ...outcome.referenced_articles]) {
      object(call); text(call.server); text(call.tool); object(call.args)
      check(call.error === null || typeof call.error === 'string'); check('result' in call)
    }
  }
  if (value.execution_status === 'FAILED') {
    object(value.error); text(value.error.code); text(value.error.message); check(typeof value.error.retryable === 'boolean')
    check(answer.kind === 'failed')
  } else check(value.error === null && answer.kind !== 'failed')
  // 검증 후 원본 객체를 반환한다. 근거와 전체 응답을 재구성하거나 잘라내지 않는다.
  return value as unknown as GenerationResponse
}
function messages(value: unknown, id: string) {
  check(Array.isArray(value))
  let previous = 0
  for (const item of value) {
    object(item); check(Number.isSafeInteger(item.seq) && Number(item.seq) > previous); previous = Number(item.seq)
    uuid(item.message_id); text(item.content); time(item.created_at)
    if (item.role === 'user') check(item.response === null)
    else {
      check(item.role === 'assistant')
      const response = parseGenerationResponse(item.response, id)
      check(response.execution_status !== 'FAILED' && response.response_id === item.message_id && response.answer.text === item.content)
    }
  }
}
export function parseSnapshot(value: unknown, id?: string): ConversationSnapshot {
  envelope(value); summary(value); uuid(value.conversation_id); check(!id || value.conversation_id === id)
  messages(value.messages, value.conversation_id); cursor(value.next_cursor, true)
  return value as unknown as ConversationSnapshot
}
export function parseConversationList(value: unknown): ConversationList {
  envelope(value); check(Array.isArray(value.items)); value.items.forEach(summary); cursor(value.next_cursor)
  return value as unknown as ConversationList
}
export function parseMessagePage(value: unknown, id: string): MessagePage {
  envelope(value); check(value.conversation_id === id); messages(value.messages, id); cursor(value.next_cursor, true)
  return value as unknown as MessagePage
}
export function parseSavedResponse(value: unknown, id: string, responseId: string): SavedResponse {
  object(value); text(value.query)
  const response = parseGenerationResponse(value.response, id); check(response.response_id === responseId && response.execution_status !== 'FAILED')
  return value as unknown as SavedResponse
}
