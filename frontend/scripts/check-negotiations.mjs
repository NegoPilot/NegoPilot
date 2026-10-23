import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// 협상 작업 API 모듈의 응답 파서와 PATCH changes 생성 함수를 검사한다.
async function moduleUrl(path, replacements = []) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8')
  for (const [before, after] of replacements) source = source.replaceAll(before, after)
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const client = `
export const API_PATHS = { negotiations: '/api/negotiations', negotiationExports: '/api/negotiation-exports' }
export class ApiError extends Error { constructor(status, code, message, retryable) { super(message); this.status = status; this.code = code; this.retryable = retryable } }
export async function request() { throw new Error('not used') }`
const contract = `export function parseReviewResponse(v) { if (v.schema_version !== '0.3') throw new Error('bad'); return { ...v } }`
const api = await import(await moduleUrl('../src/api/negotiations.ts', [
  ["from './client'", JSON.stringify(`data:text/javascript;base64,${Buffer.from(client).toString('base64')}`)],
  ["from './conversation-contract'", JSON.stringify(`data:text/javascript;base64,${Buffer.from(contract).toString('base64')}`)],
]))

const review = {
  schema_version: '0.3', execution_status: 'COMPLETED', review_id: 'w1', review_revision: 1, base_revision: null,
  input_snapshot: { unit_price: 2050, energy_ratio: null, supplier_size: {} }, eligibility: 'conditional', created_at: '2026-10-23T00:00:00Z',
  legal_review: { review_id: 'a1', version: 'p0.1', input_hash: 'h', assessment: { status: 'needs_information', findings: [], missing_fields: [], constraints: [] } },
}
const parsed = api.parseStoredReview(review)
assert.equal(parsed.eligibility, 'conditional')
assert.equal(parsed.input_snapshot.energy_ratio, null)
assert.deepEqual(parsed.input_snapshot.supplier_size, {})
// 저장 API는 서명 토큰을 주지 않는다. 섞여 오면 거절한다.
assert.throws(() => api.parseStoredReview({ ...review, legal_review: { ...review.legal_review, review_token: 'x' } }))
assert.throws(() => api.parseStoredReview({ ...review, review_token: 'x' }))
assert.throws(() => api.parseStoredReview({ ...review, eligibility: 'maybe' }))
assert.equal(api.parseStoredReview({ ...review, execution_status: 'FAILED' }).review_revision, 0)

const external = { proposal_id: 'p1', proposal_revision: 2, external_document: { unit_price: 1948, title: '제안서' }, validation: { status: 'checked', checked_revision: 2, conclusion: 'conditional' } }
assert.equal(api.parseExternal(external).proposal_revision, 2)
for (const key of ['price_range', 'concessions', 'objections', 'writing', 'target_price', 'max_price']) {
  assert.throws(() => api.parseExternal({ ...external, [key]: [] }), `top-level ${key}`)
  assert.throws(() => api.parseExternal({ ...external, external_document: { ...external.external_document, [key]: 1 } }), `document ${key}`)
}
assert.throws(() => api.parseProposal({ proposal_id: 'p1' }))
assert.equal(api.parseValidation({ status: 'recheck_required', checked_revision: 1 }).conclusion, null)
assert.throws(() => api.parseValidation({ status: 'done' }))

const view = {
  proposal_id: 'p1', proposal_revision: 1, head_revision: 1, edit_history: [], available_export_formats: { internal: [], external: ['pdf'] },
  validation: { status: 'checked' },
  proposal: { status: 'offer', writing: { strategy: 's', opening_message: 'o', objection_scripts: ['a'] }, offer: {
    unit_price: 1948, payment_days: 60, price_linkage: true, contract_days: 365, linkage_waived_by_agreement: false, notes: null,
    price_range: [{ name: 'opening', unit_price: 1948 }, { name: 'settlement', unit_price: 1999 }, { name: 'walk_away', unit_price: 2050 }],
    concessions: ['c1'], objections: [{ issue: 'i', response: 'r' }] } },
}
const before = api.editStateFrom(api.parseProposal(view), external)
assert.deepEqual(api.buildChanges(before, structuredClone(before)), {})
const after = structuredClone(before)
after.offer.payment_days = 45; after.offer.walk_away = 2100; after.offer.notes = '납품 협의'
after.writing.strategy = 's2'; after.external.cover_letter = '안녕하세요'; after.external.linkage_terms['조정 주기'] = '분기'
assert.deepEqual(api.buildChanges(before, after), {
  offer: { payment_days: 45, notes: '납품 협의', price_range: { walk_away: 2100 } },
  writing: { strategy: 's2' },
  external: { cover_letter: '안녕하세요', linkage_terms: { '기준 지표': '', '조정 요건(변동률)': '', '조정 주기': '분기', '조정 산식': '' } },
})
console.log('negotiation API checks passed')
