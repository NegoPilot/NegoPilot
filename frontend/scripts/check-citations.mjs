import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'
import { compileTemplate, parse } from '@vue/compiler-sfc'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createMemoryHistory, createRouter } from 'vue-router'

async function source(path) {
  return readFile(new URL(path, import.meta.url), 'utf8')
}
function transpile(value) {
  return ts.transpileModule(value, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
}
async function moduleFrom(value) {
  return import(`data:text/javascript;base64,${Buffer.from(value).toString('base64')}`)
}
const { splitCitations } = await moduleFrom(transpile(await source('../src/utils/evidence.ts')))
const contract = await moduleFrom(transpile(await source('../src/api/conversation-contract.ts')))
const text = value => ({ type: 'text', value })
const cite = n => ({ type: 'cite', n })
const cases = [
  ['', []],
  ['인용 없는 답변', [text('인용 없는 답변')]],
  ['답변 [1].', [text('답변 '), cite(1), text('.')]],
  ['[1] [2]', [cite(1), text(' '), cite(2)]],
  ['[1,2,3,4,5]', [cite(1), text(' '), cite(2), text(' '), cite(3), text(' '), cite(4), text(' '), cite(5)]],
  ['앞 [3, 1, 3] 뒤', [text('앞 '), cite(3), text(' '), cite(1), text(' '), cite(3), text(' 뒤')]],
  ['[ 1,\n 2\t, 3 ]', [cite(1), text(' '), cite(2), text(' '), cite(3)]],
  ['[1, 2]와 [3] [4,5]', [cite(1), text(' '), cite(2), text('와 '), cite(3), text(' '), cite(4), text(' '), cite(5)]],
]
for (const value of ['[]', '[1,]', '[,1]', '[1,,2]', '[1-3]', '[1.2,3]', '[-1,2]', '[E1,E2]', '[안내]', '일반 숫자 1, 2, 3']) {
  cases.push([value, [text(value)]])
}
for (const [input, expected] of cases) assert.deepEqual(splitCitations(input), expected, input)

// Compile the actual panel template and its link/history helpers. This keeps the
// regression check independent of a browser runner without copying rendering logic.
const panel = parse(await source('../src/components/AiAssistantPanel.vue')).descriptor
const compiled = compileTemplate({ source: panel.template.content, filename: 'AiAssistantPanel.vue', id: 'citation-check', ssr: true, ssrCssVars: [] })
assert.deepEqual(compiled.errors, [])
const renderer = compiled.code.replaceAll('"vue/server-renderer"', JSON.stringify(import.meta.resolve('vue/server-renderer')))
  .replaceAll('"vue"', JSON.stringify(import.meta.resolve('vue')))
const { ssrRender } = await moduleFrom(renderer)
const ast = ts.createSourceFile('panel.ts', panel.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const helperNames = ['rows', 'evidenceHref', 'citationHref']
const helpers = ast.statements.filter(node => ts.isFunctionDeclaration(node) && helperNames.includes(node.name?.text))
assert.equal(helpers.length, helperNames.length)
const router = createRouter({ history: createMemoryHistory(), routes: [
  { name: 'evidence-detail', path: '/conversations/:conversationId/responses/:responseId/evidence/:evidenceId', component: {} },
] })
const { rows, evidenceHref, citationHref } = new Function('router', 'props',
  `${transpile(helpers.map(node => node.getText(ast)).join('\n'))}\nreturn { ${helperNames.join(', ')} }`,
)(router, { backPath: '/chat', rfq: null })

const conversationId = '56f51af3-50dc-42e2-a603-f7cf637df417'
const responseId = '52a3f43d-cc53-4e9f-a17d-0da24fa2af3a'
const stamp = '2026-10-21T05:00:00Z'
function response(body, ids) {
  return {
    schema_version: '0.3', request_id: conversationId, conversation_id: conversationId, response_id: responseId,
    mode: 'live', execution_status: 'COMPLETED', generation_status: 'COMPLETED', context: { selected_supplier_ids: [] },
    decomposition: { original_query: '질문', sub_queries: [] },
    routing: { schema_version: '0.1', status: 'routed', halted: false, halt_reason: null, outcomes: [] },
    answer: {
      kind: 'answer', text: body, grounded: ids.length > 0, options: [], notices: [], verification: null,
      citations: ids.map((id, index) => ({ n: index + 1, evidence_id: id, label: `근거 ${id}`, source_url: null })),
      evidence: ids.map(id => ({ id, sub_query: 'q1', source: 'law', kind: 'article', label: `근거 ${id}`, text: `본문 ${id}`, citable: true, source_url: null, meta: {} })),
    },
    error: null,
  }
}
function snapshot(value) {
  return {
    schema_version: '0.4', storage: 'database', conversation_id: conversationId, title: '저장된 대화',
    created_at: stamp, updated_at: stamp, message_count: 2, context: { selected_supplier_ids: [] }, next_cursor: null,
    messages: [
      { seq: 1, message_id: conversationId, role: 'user', content: '질문', created_at: stamp, response: null },
      { seq: 2, message_id: responseId, role: 'assistant', content: value.answer.text, created_at: stamp, response: value },
    ],
  }
}
async function renderMessages(messages) {
  return renderToString(createSSRApp({
    ssrRender,
    setup: () => ({
      messages, splitCitations, citationHref, evidenceHref, supplierSummary: () => null,
      conversationId, historyOpen: false, historyItems: [], historyLoading: false, historyError: null,
      historyErrorText: null, historyCursor: null, sending: false, closable: false, loading: false,
      nextCursor: null, loaded: true, suggestions: [], errorText: null, draft: '', QUERY_MAX: 4000, canSend: false,
    }),
  }))
}
function anchors(html, className) {
  return [...html.matchAll(new RegExp(`<a\\b([^>]*class="${className}"[^>]*)>([\\s\\S]*?)<\\/a>`, 'g'))]
    .map(match => ({ href: /href="([^"]+)"/.exec(match[1])?.[1], body: match[2], attributes: match[1] }))
}
let renderedCases = 0
for (const [body, ids, inlineNumbers] of [
  ['인용 없는 답변', [], []],
  ['단일 근거 [1]', ['E6'], [1]],
  ['묶음 근거 [1, 2, 3, 4, 5]', ['E6', 'E1', 'E4', 'E9', 'E2'], [1, 2, 3, 4, 5]],
  ['반복과 순서 [3, 1, 3]', ['E6', 'E1', 'E4'], [3, 1, 3]],
  ['일부 근거 누락 [1, 9, 2]', ['E6', 'E1'], [1, 2]],
  ['연결 정보 없음 [1, 2]', [], []],
]) {
  const value = response(body, ids)
  // Legacy responses have no suggested_questions field and preserve their raw text.
  const persisted = JSON.parse(JSON.stringify(snapshot(value)))
  const before = JSON.stringify(persisted)
  const restored = contract.parseSnapshot(persisted, conversationId)
  const restoredResponse = restored.messages[1].response
  assert.equal(contract.parseSavedResponse({ query: '질문', response: restoredResponse }, conversationId, responseId).response, restoredResponse)
  const historyHtml = await renderMessages(rows(restored.messages))
  const liveHtml = await renderMessages([{ key: responseId, role: 'assistant', text: body, response: value }])
  const links = anchors(historyHtml, 'cite')
  assert.deepEqual(links.map(link => Number(/\[(\d+)\]/.exec(link.body)[1])), inlineNumbers, body)
  assert.deepEqual(links.map(link => link.href), inlineNumbers.map(n => evidenceHref(value, ids[n - 1])), body)
  assert.deepEqual(links, anchors(liveHtml, 'cite'), 'Live and restored responses render the same inline links')
  const evidenceLinks = anchors(historyHtml, 'evidence-link')
  assert.equal(evidenceLinks.length, ids.length, 'Separate evidence list is preserved')
  assert.deepEqual(evidenceLinks.map(link => link.href), ids.map(id => evidenceHref(value, id)))
  for (const link of [...links, ...evidenceLinks]) {
    assert.match(link.attributes, /target="_blank"/)
    assert.match(link.attributes, /rel="noopener noreferrer"/)
  }
  assert.equal(historyHtml.includes('class="evidence"'), ids.length > 0)
  if (body.includes('[1, 9, 2]')) assert.match(historyHtml, /\[9\]/, 'An unknown reference remains visible without a fabricated link')
  assert.equal(JSON.stringify(persisted), before, 'Rendering never rewrites the saved response')
  renderedCases += 1
}
console.log(`Citation regression checks passed: ${cases.length} parser cases and ${renderedCases} live/history rendering cases with separate evidence lists and validated link targets.`)
