import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

async function moduleUrl(path, replacements = []) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8')
  for (const [before, after] of replacements) source = source.replaceAll(before, after)
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const formatUrl = await moduleUrl('../src/utils/format.ts')
const search = await import(await moduleUrl('../src/utils/search.ts', [["'./format'", JSON.stringify(formatUrl)]]))
const format = await import(formatUrl)
const defaults = search.emptySearchValues()
assert.deepEqual(search.searchValuesToParams(defaults), { page: 0, size: 20, sort: 'name', direction: 'asc' })
assert.deepEqual(search.searchValuesToQuery(defaults), {})
assert.equal(search.searchValuesToParams({ ...defaults, material: 'AL6061' }).material, 'AL6061')
assert.equal(search.searchValuesToParams({ ...defaults, material: 'AL6061' }).process, undefined)
assert.equal(search.searchValuesToParams({ ...defaults, process: 'CNC가공' }).material, undefined)
assert.equal(search.searchValuesToParams({ ...defaults, q: '  SUP-002  ' }).q, 'SUP-002')
assert.equal(search.searchValuesToParams({ ...defaults, q: '  ' }).q, undefined)
const conditions = {
  ...defaults, q: '테스트_% 협력사', process: 'CNC가공', material: 'AL6061', region: '서울',
  iatfRequired: true, iso14001Required: true, minMonthlyCapacity: '50,000',
  page: 4, size: 50, sort: 'on_time_delivery_rate', direction: 'desc',
}
const query = search.searchValuesToQuery(conditions)
// Reload/back navigation must reconstruct the server-wide filters and ordering with the page intact.
assert.deepEqual(search.searchValuesToParams(search.searchValuesFromQuery(query)), {
  q: '테스트_% 협력사', process: 'CNC가공', material: 'AL6061', region: '서울',
  iatf_required: true, iso14001_required: true, min_monthly_capacity: 50000,
  page: 4, size: 50, sort: 'on_time_delivery_rate', direction: 'desc',
})
assert.equal(search.searchValuesToQuery({ ...conditions, page: 0 }).page, undefined)
assert.equal(search.searchValuesToParams({ ...defaults, minMonthlyCapacity: 'bad' }), null)
assert.equal(search.searchValuesToParams({ ...defaults, minMonthlyCapacity: '0' }), null)
assert.equal(search.searchValuesToParams({ ...defaults, minMonthlyCapacity: '10000001' }), null)
assert.equal(search.searchValuesFromQuery({ page: '-1', size: '200', sort: 'unknown', direction: 'wrong' }).page, 0)
assert.equal(search.searchValuesFromQuery({ page: '1.5' }).page, 0)
assert.equal(search.searchValuesFromQuery({ page: '999999999999999999999' }).page, 0)
assert.equal(search.searchValuesFromQuery({ size: '200' }).size, 20)
assert.equal(search.searchValuesFromQuery({ sort: 'latest_quote_price', process: 'CNC가공' }).sort, 'name')
assert.equal(search.searchValuesFromQuery({ sort: 'latest_quote_price', process: 'CNC가공', material: 'AL6061' }).sort, 'latest_quote_price')
assert.deepEqual(search.pageNumbers(0, 0), [])
assert.deepEqual(search.pageNumbers(0, 1), [0])
assert.deepEqual(search.pageNumbers(0, 2), [0, 1])
assert.deepEqual(search.pageNumbers(8, 20), [0, 'gap', 6, 7, 8, 9, 10, 'gap', 19])
assert.deepEqual(search.pageNumbers(19, 20), [0, 'gap', 15, 16, 17, 18, 19])
assert.equal(format.formatCapacity(0), '0 EA/월')
assert.equal(format.formatPercent(0), '0%')
assert.equal(format.formatPercent(98), '98%')
assert.equal(format.formatKrw(null), '정보 없음')
assert.equal(format.formatPpm(0), '0 ppm')

const calls = []
const response = { schema_version: '0.4', mode: 'live', data: { items: [], total: 31, page: 1, size: 20, returned_count: 11, total_pages: 2, sort: 'name', direction: 'asc' } }
globalThis.__supplierLookupCheck = async (url) => { calls.push(url); return response }
try {
  const api = await import(await moduleUrl('../src/api/suppliers.ts', [[
    "import { API_PATHS, request } from './client'",
    "const API_PATHS = { suppliers: '/api/suppliers', businessCatalog: '/api/business/catalog' }; const request = globalThis.__supplierLookupCheck",
  ]]))
  assert.equal(await api.searchSuppliers(search.searchValuesToParams(defaults)), response.data)
  const all = new URL(calls.pop(), 'http://test.local')
  assert.equal(all.pathname, '/api/suppliers')
  assert.deepEqual(Object.fromEntries(all.searchParams), { page: '0', size: '20', sort: 'name', direction: 'asc' })
  await api.searchSuppliers(search.searchValuesToParams({ ...conditions, q: 'A&B_% + 협력사', page: 1 }))
  const filtered = new URL(calls.pop(), 'http://test.local')
  assert.equal(filtered.searchParams.get('q'), 'A&B_% + 협력사')
  assert.equal(filtered.searchParams.get('page'), '1')
  assert.equal(filtered.searchParams.get('min_monthly_capacity'), '50000')
  assert.equal(filtered.searchParams.has('limit'), false)
  assert.equal(filtered.searchParams.get('direction'), 'desc')
  await api.searchSuppliers(search.searchValuesToParams({ ...defaults, material: 'AL6061' }))
  const materialOnly = new URL(calls.pop(), 'http://test.local')
  assert.equal(materialOnly.searchParams.has('process'), false)
  assert.equal(materialOnly.searchParams.get('material'), 'AL6061')
  await api.getSupplier('SUP-0001')
  assert.equal(calls.pop(), '/api/suppliers/SUP-0001')
  await api.getSupplierQuotes('SUP-0001', { page: 2, size: 20, sort: 'unit_price_krw', direction: 'asc', process: 'CNC가공', material: 'AL6061' })
  const quoteUrl = new URL(calls.pop(), 'http://test.local')
  assert.equal(quoteUrl.pathname, '/api/suppliers/SUP-0001/quotes')
  assert.equal(quoteUrl.searchParams.get('page'), '2')
  assert.equal(quoteUrl.searchParams.get('sort'), 'unit_price_krw')
  assert.equal(quoteUrl.searchParams.get('process'), 'CNC가공')
} finally { delete globalThis.__supplierLookupCheck }
console.log('Supplier lookup checks passed: default all, independent filters, URL restoration, pagination, scoped price sorting, missing/zero values and paginated API requests.')

// Execute the real workspace setup with a memory router and renderer. No browser or server is started.
const { parse, compileScript } = await import('@vue/compiler-sfc')
const { createRenderer, reactive, nextTick } = await import('vue')
const route = reactive({ query: {} })
const componentCalls = { list: [], detail: [], catalog: 0 }
const context = {
  useRoute: () => route,
  useRouter: () => ({ push: async ({ query }) => { route.query = query }, replace: async ({ query }) => { route.query = query } }),
  getCatalog: async () => { componentCalls.catalog++; return { processes: [], regions: [] } },
  getSupplier: async (id) => { componentCalls.detail.push(id); return { supplier_id: id } },
  searchSuppliers: async (params) => {
    componentCalls.list.push(structuredClone(params))
    const total = 31
    const count = Math.min(params.size, Math.max(0, total - params.page * params.size))
    return { ...params, total, total_pages: Math.ceil(total / params.size), returned_count: count,
      items: Array.from({ length: count }, (_, i) => ({ supplier_id: `SUP-${params.page * params.size + i}` })) }
  },
}
globalThis.__supplierComponentCheck = context
const workspaceSource = await readFile(new URL('../src/views/SupplierWorkspaceView.vue', import.meta.url), 'utf8')
let setupSource = compileScript(parse(workspaceSource).descriptor, { id: 'supplier-component-check' }).content
setupSource = setupSource
  .replaceAll("from 'vue'", `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replaceAll('from "vue"', `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replace(/import \{[^}]+\} from 'vue-router'/, 'const { useRoute, useRouter } = globalThis.__supplierComponentCheck')
  .replace(/import \{[^}]+\} from '@\/api\/suppliers'/, 'const { getCatalog, getSupplier, searchSuppliers } = globalThis.__supplierComponentCheck')
  .replace(/import \w+ from '@\/components\/[^']+'/g, match => `const ${/import (\w+)/.exec(match)[1]} = {}`)
  .replace(/import \{[^}]+\} from '@\/stores\/assistant'/, 'const assistantOpen = false; const openAssistant = () => {}')
  .replaceAll("'@/utils/format'", JSON.stringify(formatUrl))
  .replaceAll("'@/utils/provenance'", JSON.stringify(await moduleUrl('../src/utils/provenance.ts')))
  .replaceAll("'@/utils/search'", JSON.stringify(await moduleUrl('../src/utils/search.ts', [["'./format'", JSON.stringify(formatUrl)]])))
const setupJs = ts.transpileModule(setupSource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const workspace = (await import(`data:text/javascript;base64,${Buffer.from(setupJs).toString('base64')}`)).default
const renderer = createRenderer({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}), insert() {}, remove() {},
  setElementText() {}, setText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {},
})
let state
const app = renderer.createApp({ setup() { state = workspace.setup({}, { expose() {} }); return () => null } })
async function settle() { for (let i = 0; i < 5; i++) { await Promise.resolve(); await nextTick() } }
try {
  app.mount({})
  await settle()
  assert.equal(componentCalls.list.length, 1)
  assert.deepEqual(componentCalls.list[0], { page: 0, size: 20, sort: 'name', direction: 'asc' })
  assert.equal(componentCalls.catalog, 1)
  assert.deepEqual(componentCalls.detail, [], 'Twenty list rows must not issue twenty detail requests')
  state.movePage(1)
  await settle()
  assert.equal(route.query.page, '1')
  assert.equal(componentCalls.list.at(-1).page, 1)
  state.onSubmit({ ...search.emptySearchValues(), q: '삼성', material: 'AL6061', page: 1 })
  await settle()
  assert.equal(route.query.page, undefined, 'A new search resets the page')
  assert.equal(componentCalls.list.at(-1).q, '삼성')
  assert.equal(componentCalls.list.at(-1).process, undefined)
  state.changeSort({ target: { value: 'defect_ppm' } })
  await settle()
  assert.equal(state.columnGroup.value, 'quality', 'The selected ordering field becomes visible')
  assert.equal(componentCalls.list.at(-1).sort, 'defect_ppm')
  assert.equal(componentCalls.list.at(-1).q, '삼성')
  state.movePage(1)
  await settle()
  state.toggleDirection()
  await settle()
  assert.equal(componentCalls.list.at(-1).page, 0)
  assert.equal(componentCalls.list.at(-1).direction, 'desc')
  state.changeSize({ target: { value: '50' } })
  await settle()
  assert.equal(componentCalls.list.at(-1).size, 50)
  const beforeSelection = componentCalls.list.length
  state.select({ supplier_id: 'SUP-0001' })
  await settle()
  assert.deepEqual(componentCalls.detail, ['SUP-0001'])
  assert.equal(componentCalls.list.length, beforeSelection, 'Selecting detail must not restart the list query')
  route.query = { q: '이전 검색', material: 'ADC12', page: '1', sort: 'region', direction: 'desc' }
  await settle()
  assert.equal(state.applied.value.q, '이전 검색', 'Back/forward URL state restores the previous search')
  assert.equal(componentCalls.list.at(-1).page, 1)
  assert.equal(componentCalls.list.at(-1).sort, 'region')
  assert.deepEqual(state.maxCapacityPairs({ max_monthly_capacity: 9000, matched_capabilities: [
    { process: 'CNC가공', material: 'AL6061', monthly_capacity: 9000 },
    { process: '프레스', material: 'SPCC', monthly_capacity: 1000 },
    { process: 'CNC가공', material: 'S45C', monthly_capacity: 9000 },
  ] }), ['CNC가공 / AL6061', 'CNC가공 / S45C'])
  assert.deepEqual(state.maxCapacityPairs({ max_monthly_capacity: 0, matched_capabilities: [
    { process: '주조', material: 'ADC12', monthly_capacity: 0 },
  ] }), ['주조 / ADC12'], 'A zero capacity remains associated with its actual process and material')
  assert.deepEqual(state.maxCapacityPairs({ max_monthly_capacity: null, matched_capabilities: [] }), [])
  state.onReset()
  await settle()
  assert.deepEqual(route.query, {})
  assert.deepEqual(componentCalls.list.at(-1), { page: 0, size: 20, sort: 'name', direction: 'asc' })
} finally { app.unmount(); delete globalThis.__supplierComponentCheck }
console.log('Supplier workspace component checks passed: initial load, no N+1 requests, page/filter/sort/size transitions, visible sort fields, detail selection, URL navigation and reset.')

// Historic quote filters must remain usable after a supplier's current capabilities change.
const quoteCalls = []
const historicQuote = { quote_id: 1, process: '과거 공정', material: '과거 소재', unit_price_krw: 100, quoted_at: '2025-01-01' }
const quoteProps = reactive({ supplier: {
  supplier_id: 'SUP-0001', capabilities: [{ process: '현재 공정', material: '현재 소재', monthly_capacity: 1000 }],
  recent_quotes: [{ ...historicQuote, process: '최근 공정', material: '최근 소재' }],
  provenance: { profile: 'DART', operations: 'MOCK', financials: 'DART' },
} })
globalThis.__supplierQuoteCheck = async (id, params) => {
  quoteCalls.push({ id, ...structuredClone(params) })
  return { supplier_id: id, ...params, items: [historicQuote], returned_count: 1, total: 1, total_pages: 1 }
}
const detailSource = await readFile(new URL('../src/components/SupplierDetailSection.vue', import.meta.url), 'utf8')
let quoteSetup = compileScript(parse(detailSource).descriptor, { id: 'supplier-quote-check' }).content
quoteSetup = quoteSetup
  .replaceAll("from 'vue'", `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replaceAll('from "vue"', `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replace(/import \{[^}]+\} from '@\/api\/suppliers'/, 'const getSupplierQuotes = globalThis.__supplierQuoteCheck')
  .replace(/import \w+ from '@\/components\/[^']+'/g, match => `const ${/import (\w+)/.exec(match)[1]} = {}`)
  .replaceAll("'@/utils/format'", JSON.stringify(formatUrl))
  .replaceAll("'@/utils/provenance'", JSON.stringify(await moduleUrl('../src/utils/provenance.ts')))
  .replaceAll("'@/utils/search'", JSON.stringify(await moduleUrl('../src/utils/search.ts', [["'./format'", JSON.stringify(formatUrl)]])))
const quoteSetupJs = ts.transpileModule(quoteSetup, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const detail = (await import(`data:text/javascript;base64,${Buffer.from(quoteSetupJs).toString('base64')}`)).default
let quoteState
const quoteApp = renderer.createApp({ setup() { quoteState = detail.setup(quoteProps, { expose() {} }); return () => null } })
try {
  quoteApp.mount({})
  await settle()
  assert.equal(quoteCalls.length, 0, 'Quote history loads only when opened')
  assert.deepEqual(quoteState.quoteProcessOptions.value, ['현재 공정', '최근 공정'])
  quoteState.tab.value = 'trade'
  await settle()
  assert.equal(quoteCalls.length, 1)
  assert.ok(quoteState.quoteProcessOptions.value.includes('과거 공정'), 'Loaded historic quote values also become suggestions')
  quoteState.quoteProcessInput.value = '  더 오래된 공정  '
  quoteState.quoteMaterialInput.value = '  더 오래된 소재  '
  await settle()
  assert.equal(quoteCalls.length, 1, 'Typing suggestions does not send a request for every key')
  quoteState.changeQuoteFilter()
  await settle()
  assert.equal(quoteCalls.at(-1).process, '더 오래된 공정')
  assert.equal(quoteCalls.at(-1).material, '더 오래된 소재', 'Exact historic values are accepted even when absent from all suggestions')
  assert.equal(quoteState.quotePriceSortAvailable.value, true)
  quoteState.quoteSort.value = 'unit_price_krw'
  await settle()
  assert.equal(quoteCalls.at(-1).sort, 'unit_price_krw')
  quoteState.quoteProcessInput.value = ''
  quoteState.changeQuoteFilter()
  await settle()
  assert.equal(quoteCalls.at(-1).process, undefined)
  assert.equal(quoteCalls.at(-1).material, '더 오래된 소재', 'Material-only history search remains available')
  assert.equal(quoteCalls.at(-1).sort, 'quoted_at', 'Removing a price scope falls back to chronological ordering')
  assert.equal(quoteCalls.at(-1).page, 0)
  quoteProps.supplier = { ...quoteProps.supplier, supplier_id: 'SUP-0002' }
  await settle()
  assert.equal(quoteState.tab.value, 'overview')
  assert.equal(quoteState.quoteProcessInput.value, '')
  assert.equal(quoteState.quoteMaterialInput.value, '')
  assert.equal(quoteState.quotes.value, null)
} finally { quoteApp.unmount(); delete globalThis.__supplierQuoteCheck }
console.log('Historic quote component checks passed: optional exact-text filters, suggestions, explicit apply, scoped price ordering, material-only search and supplier-change reset.')
