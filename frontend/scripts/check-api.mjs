import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// Existing TypeScript dependency is sufficient for these API-boundary regression checks.
async function moduleFrom(path, replacements = []) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8')
  for (const [before, after] of replacements) source = source.replaceAll(before, after)
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}
const contract = await moduleFrom('../src/api/conversation-contract.ts')
const client = await moduleFrom('../src/api/client.ts', [['import.meta.env.VITE_API_TIMEOUT_MS', 'undefined']])
const evidence = await moduleFrom('../src/utils/evidence.ts')
const provenance = await moduleFrom('../src/utils/provenance.ts')
const id = '56f51af3-50dc-42e2-a603-f7cf637df417'
const rid = '52a3f43d-cc53-4e9f-a17d-0da24fa2af3a'
const stamp = '2026-10-21T05:00:00Z'
const response = {
  schema_version: '0.3', request_id: id, conversation_id: id, response_id: rid,
  mode: 'live', execution_status: 'COMPLETED', generation_status: 'COMPLETED', context: { selected_supplier_ids: [] },
  decomposition: { original_query: '질문', sub_queries: [] },
  routing: { schema_version: '0.1', status: 'routed', halted: false, halt_reason: null, outcomes: [] },
  answer: { kind: 'answer', text: '답변', citations: [], options: [], grounded: true, notices: [], evidence: [], verification: { raw: 'preserved' } },
  error: null, future_metadata: { preserved: true },
}
const summary = { conversation_id: id, title: '질문', created_at: stamp, updated_at: stamp, message_count: 2, context: { selected_supplier_ids: [] } }
const messages = [
  { seq: 1, message_id: id, role: 'user', content: '질문', created_at: stamp, response: null },
  { seq: 2, message_id: rid, role: 'assistant', content: '답변', created_at: stamp, response },
]
const snapshot = { ...summary, schema_version: '0.4', storage: 'database', messages, next_cursor: null }
assert.equal(contract.parseSnapshot(snapshot, id), snapshot)
assert.equal(contract.parseSnapshot(snapshot, id).messages[1].response, response)
assert.equal(contract.parseGenerationResponse(response, id), response)
assert.equal(contract.parseSavedResponse({ query: '질문', response }, id, rid).response, response)
assert.equal(contract.parseMessagePage({ schema_version: '0.4', storage: 'database', conversation_id: id, messages, next_cursor: '1' }, id).next_cursor, '1')
assert.equal(contract.parseConversationList({ schema_version: '0.4', storage: 'database', items: [summary], next_cursor: 'opaque_cursor' }).items.length, 1)
// Optional recommendations survive raw response and DB history parsing without a count cap.
for (const questions of [[], ['납기 실적도 알려줘'], ['재무 상태를 알려줘', '납기 실적도 알려줘', '품질 지표도 알려줘', '최근 견적도 알려줘'], ['😀'.repeat(120)]]) {
  const recommended = structuredClone(response)
  recommended.answer.suggested_questions = questions
  assert.equal(contract.parseGenerationResponse(recommended), recommended)
  assert.deepEqual(recommended.answer.suggested_questions, questions)
  const saved = structuredClone(snapshot)
  saved.messages[1].response = recommended
  assert.equal(contract.parseSnapshot(saved).messages[1].response, recommended)
  assert.equal(contract.parseSavedResponse({ query: '질문', response: recommended }, id, rid).response, recommended)
}
for (const questions of [null, undefined, '질문', {}, [null], [42], [''], [' '], ['앞 공백 '], ['\t질문'], ['가'.repeat(121)], ['😀'.repeat(121)], ['같은 질문', '같은 질문']]) {
  const invalid = structuredClone(response)
  invalid.answer.suggested_questions = questions
  assert.throws(() => contract.parseGenerationResponse(invalid))
}
// Match Python whitespace plus BOM at the edges without rewriting internal content.
for (const whitespace of ['\ufeff', '\u0085', '\u001c', '\u001d', '\u001e', '\u001f']) {
  for (const question of [whitespace, `${whitespace}질문`, `질문${whitespace}`]) {
    const invalid = structuredClone(response)
    invalid.answer.suggested_questions = [question]
    assert.throws(() => contract.parseGenerationResponse(invalid))
  }
  const internal = structuredClone(response)
  internal.answer.suggested_questions = [`후속${whitespace}질문`]
  assert.equal(contract.parseGenerationResponse(internal), internal)
}
for (const change of [
  value => { value.schema_version = '0.3'; value.storage = 'memory' },
  value => { value.messages[1].seq = 1 },
  value => { value.messages[1].response = null },
  value => { value.messages[1].response.conversation_id = rid },
  value => { value.messages[1].response.answer.options = null },
  value => { value.messages[1].response.routing.outcomes = null },
  value => { value.next_cursor = 'not-a-sequence' },
]) {
  const invalid = structuredClone(snapshot); change(invalid); assert.throws(() => contract.parseSnapshot(invalid, id))
}
const failed = structuredClone(response)
failed.execution_status = 'FAILED'; failed.generation_status = 'FAILED'; failed.answer.kind = 'failed'
failed.error = { code: 'GENERATION_FAILED', message: 'failed', retryable: false }
assert.equal(contract.parseGenerationResponse(failed).execution_status, 'FAILED')
assert.throws(() => contract.parseSavedResponse({ query: '질문', response: failed }, id, rid))
const failedSnapshot = structuredClone(snapshot); failedSnapshot.messages[1].response = failed
assert.throws(() => contract.parseSnapshot(failedSnapshot))

// The MCP returns flat quality/contract fields and KRW amounts. Model execution mode is not data provenance.
const supplierPayload = {
  supplier_id: 'SUP-0001', name: '테스트 기업', source: 'DART', profile_origin_source: 'DART',
  certification_source: 'MOCK', capability_source: 'MANUAL', quality_source: 'MOCK',
  iatf16949: true, iso14001: false, defect_ppm: 141, on_time_rate: 0.98,
  payment_days: 60, price_linkage: false,
  capabilities: [{ process: '주조', material: '알루미늄', monthly_capacity: 50_000 }],
  financials: [
    { bsns_year: 2025, fs_div: 'CFS', revenue: 123_456_789_000, operating_income: 4_321_000_000, total_liabilities: 300, total_equity: 200, source: 'DART' },
    { bsns_year: 2024, revenue: null, operating_income: null, total_liabilities: 300, total_equity: 0 },
  ],
  recent_quotes: [{ process: '주조', material: '알루미늄', unit_price: 12_345, quoted_at: '2026-10-01' }],
}
const supplierEvidence = { source: 'business', kind: 'supplier', text: JSON.stringify(supplierPayload), meta: {} }
const parsed = evidence.parseSupplierEvidence(supplierEvidence)
assert.equal(parsed.quality.defect_ppm, 141)
assert.equal(parsed.quality.on_time_delivery_rate, 98)
assert.equal(parsed.financials[0].revenue_krw, 123_456_789_000)
assert.equal(parsed.financials[0].operating_profit_krw, 4_321_000_000)
assert.equal(parsed.financials[0].debt_ratio, 150)
assert.equal(parsed.financials[1].debt_ratio, null)
assert.equal(parsed.financials[1].source, 'UNKNOWN')
assert.equal(parsed.provenance.financials, 'MIXED')
assert.equal(parsed.recent_quotes[0].unit_price_krw, 12_345)
assert.equal(parsed.recent_quotes[0].source, 'UNKNOWN')
assert.deepEqual(parsed.existing_contract, { payment_days: 60, price_linkage: false })
assert.deepEqual(parsed.provenance, {
  profile: 'DART', profile_origin: 'DART', certifications: 'MOCK', capabilities: 'MANUAL', quality: 'MOCK',
  financials: 'MIXED', quotes: 'UNKNOWN', contracts: 'UNKNOWN',
})
assert.equal(supplierEvidence.text, JSON.stringify(supplierPayload))
const parseSupplier = payload => evidence.parseSupplierEvidence({ ...supplierEvidence, text: JSON.stringify(payload) })
assert.equal(parseSupplier({ supplier_id: 'S', source: 'DART', on_time_rate: null }).quality.on_time_delivery_rate, null)
const missingSources = parseSupplier({ supplier_id: 'S', source: 'DART' })
assert.equal(missingSources.provenance.profile_origin, 'UNKNOWN')
assert.equal(missingSources.provenance.certifications, 'UNKNOWN')
assert.equal(missingSources.provenance.capabilities, 'UNKNOWN')
assert.equal(missingSources.provenance.quality, 'UNKNOWN')
assert.equal(missingSources.provenance.financials, 'UNKNOWN')
const canonical = parseSupplier({
  supplier_id: 'S', quality: { defect_ppm: null, on_time_delivery_rate: 98 },
  financials: [{ year: 2025, revenue_krw: 1000, operating_profit_krw: 20, debt_ratio: 250, source: 'MOCK' }],
  recent_quotes: [{ process: '주조', material: '알루미늄', unit_price_krw: 9, quoted_at: '2026-10-01', source: 'MANUAL' }],
  provenance: { profile: 'DART', operations: 'MOCK', financials: 'DART' },
})
assert.equal(canonical.quality.on_time_delivery_rate, 98)
assert.equal(canonical.financials[0].debt_ratio, 250)
assert.equal(canonical.financials[0].revenue_krw, 1000)
assert.equal(canonical.provenance.financials, 'MOCK')
assert.equal(canonical.provenance.certifications, 'MOCK')
assert.equal(canonical.recent_quotes[0].unit_price_krw, 9)
assert.equal(canonical.recent_quotes[0].source, 'MANUAL')
assert.equal(parseSupplier({ supplier_id: 'S', quality: { on_time_delivery_rate: null, on_time_rate: 0.98 } }).quality.on_time_delivery_rate, null)
assert.equal(parseSupplier({ supplier_id: 'S', on_time_rate: 1.5 }).quality.on_time_delivery_rate, null)
assert.equal(evidence.parseSupplierEvidence({ ...supplierEvidence, text: '{truncated' }), null)
const fields = evidence.parseEvidenceFields({ ...supplierEvidence, kind: 'supplier_hit', text: JSON.stringify({ ...supplierPayload, debt_ratio: 1.5, unit_price: 12_345 }) })
assert.equal(fields.find(row => row.key === 'iatf16949').source, 'MOCK')
assert.equal(fields.find(row => row.key === 'on_time_rate').value, '98%')
assert.equal(fields.find(row => row.key === 'debt_ratio').value, '150%')
assert.equal(fields.find(row => row.key === 'unit_price').value, '12,345')
assert.equal(fields.find(row => row.key === 'unit_price').source, 'UNKNOWN')
assert.equal(provenance.provenanceLabel('STUB'), 'STUB 테스트 응답')
assert.equal(provenance.provenanceLabel('MANUAL'), '관리자 입력')
assert.equal(provenance.provenanceLabel('UNKNOWN'), '출처 정보 없음')

const originalFetch = globalThis.fetch
const calls = []
try {
  globalThis.fetch = async (path, init) => {
    calls.push({ path, init })
    if (path === '/api/auth/csrf') return Response.json({ header_name: 'X-CSRF-TOKEN', token: 'test-csrf' })
    return new Response(null, { status: 204 })
  }
  assert.equal(await client.request('/delete', { method: 'DELETE', headers: { 'Idempotency-Key': id } }), undefined)
  assert.equal(calls.length, 2)
  assert.equal(calls[1].init.headers.get('X-CSRF-TOKEN'), 'test-csrf')
  assert.equal(calls[1].init.headers.get('Idempotency-Key'), id)
  assert.equal(calls[1].init.credentials, 'same-origin')
  globalThis.fetch = async () => Response.json({ request_id: null, error: { code: 'FORBIDDEN', message: 'denied', retryable: false } }, { status: 403 })
  await assert.rejects(client.request('/private'), error => error.code === 'FORBIDDEN' && error.status === 403)
  globalThis.fetch = async (_path, init) => ({ ok: true, status: 200, text: () => new Promise((_, reject) => { if (init.signal.aborted) reject(new DOMException('Aborted', 'AbortError')); else init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }) }) })
  await assert.rejects(client.request('/slow-body', {}, 5), error => error.code === 'CLIENT_TIMEOUT')
  const abort = new AbortController()
  const cancelled = client.request('/cancelled', { signal: abort.signal })
  abort.abort()
  await assert.rejects(cancelled, error => error.code === 'REQUEST_CANCELLED')
} finally { globalThis.fetch = originalFetch }
console.log('API regression checks passed: DB history, raw response, optional suggested questions without a count cap, flat MCP fields, units, field provenance, FAILED, CSRF, cookies, 204, timeout and cancellation.')
const standalone = structuredClone(response)
delete standalone.conversation_id; delete standalone.response_id
assert.equal(contract.parseReviewResponse(standalone), standalone)
assert.throws(() => contract.parseGenerationResponse(standalone))
console.log('Standalone review response checks passed')
