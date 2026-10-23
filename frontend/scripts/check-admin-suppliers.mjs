import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// Exercise the admin supplier API module and form conversion without a server or browser.
async function moduleUrl(path, replacements = []) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8')
  for (const [before, after] of replacements) source = source.replaceAll(before, after)
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const clientUrl = await moduleUrl('../src/api/client.ts', [['import.meta.env.VITE_API_TIMEOUT_MS', 'undefined']])
const api = await import(await moduleUrl('../src/api/admin-suppliers.ts', [["from './client'", `from '${clientUrl}'`]]))
const catalogUrl = await moduleUrl('../src/data/dataCatalog.ts')
const form = await import(await moduleUrl('../src/utils/supplierForm.ts', [["from '@/data/dataCatalog'", `from '${catalogUrl}'`]]))

const root = '/api/admin/suppliers'
const stamp = '2026-10-21T00:00:00Z'
const supplier = {
  supplier_id: 'SUP-0151', source: 'MANUAL', name: '수동기업', region: '부산',
  corp_code: null, stock_code: null, induty_code: null, employees: 12, revenue_eok: null,
  founded_year: 2020, biz_no_masked: '123-45-*****', iatf16949: true, iso14001: false,
  status: 'ACTIVE', version: 0, profile_origin_source: 'MANUAL', certification_source: 'MANUAL',
  capability_source: 'MANUAL', quality_source: 'MANUAL', profile_original: {}, created_at: stamp, updated_at: stamp,
}
const detail = {
  supplier, capabilities: [{ process: '프레스', material: 'SPCC', monthly_capacity: 1000 }],
  quality: null, financials: [], contract: null, quotes: [], audit: [],
}
const invalidResponse = error => error.status === 502 && error.code === 'INVALID_RESPONSE'
const invalidRequest = error => error.status === 400 && error.code === 'VALIDATION_ERROR'
const calls = []
const originalFetch = globalThis.fetch
let respond
function serve(body, status = 200) {
  calls.length = 0
  respond = async path => path === '/api/auth/csrf'
    ? Response.json({ header_name: 'X-CSRF-TOKEN', token: 'test-csrf' })
    : Response.json(body, { status })
}
const sent = () => calls.find(c => c.path !== '/api/auth/csrf')

try {
  globalThis.fetch = async (path, init) => {
    calls.push({ path, init })
    return respond(path, init)
  }

  // 목록: 빈 검색어는 보내지 않고, 페이지와 필터는 계약의 이름으로 보낸다.
  serve({ items: [], total: 0, page: 0, size: 20 })
  await api.listSuppliers({ q: '   ' })
  assert.equal(calls.length, 1, 'GET must not obtain a CSRF token')
  assert.equal(calls[0].path, `${root}?page=0&size=20`)
  serve({ items: [], total: 3, page: 1, size: 2 })
  await api.listSuppliers({ q: ' 한빛 ', source: 'MANUAL', status: 'INACTIVE', page: 1, size: 2 })
  assert.equal(calls[0].path, `${root}?page=1&size=2&q=%ED%95%9C%EB%B9%9B&source=MANUAL&status=INACTIVE`)
  await assert.rejects(api.listSuppliers({ size: 101 }), invalidRequest)
  serve({ items: [], total: 0, page: 3, size: 20 })
  await assert.rejects(api.listSuppliers(), invalidResponse, 'page must match the request')

  // 상세는 요청한 ID와 같아야 한다.
  serve(detail)
  assert.equal((await api.getSupplier('SUP-0151')).supplier.name, '수동기업')
  await assert.rejects(api.getSupplier('SUP-0152'), invalidResponse)
  await assert.rejects(api.getSupplier('../members'), invalidRequest)

  // 수정은 CSRF 토큰과 version을 보낸다. 바뀐 영역만 담는다.
  serve(detail)
  await api.patchSupplier('SUP-0151', 4, { profile: { employees: 20 }, certifications: {} })
  assert.equal(calls[0].path, '/api/auth/csrf')
  assert.equal(sent().init.method, 'PATCH')
  assert.equal(sent().init.headers.get('X-CSRF-TOKEN'), 'test-csrf')
  assert.deepEqual(JSON.parse(sent().init.body), { version: 4, profile: { employees: 20 } })
  await assert.rejects(api.patchSupplier('SUP-0151', 4, { profile: {} }), invalidRequest)

  serve(detail)
  await api.addQuotes('SUP-0151', 3, [{ process: '프레스', material: 'SPCC', unit_price: 1200, quoted_at: '2026-01-02' }])
  assert.equal(sent().path, `${root}/SUP-0151/quotes`)
  assert.equal(sent().init.method, 'POST')
  await assert.rejects(api.addQuotes('SUP-0151', 3, []), invalidRequest)

  serve(detail)
  await assert.rejects(api.setSupplierStatus('SUP-0151', 0, 'INACTIVE'), invalidResponse, 'status must be applied')
  serve({ request_id: null, error: { code: 'VERSION_CONFLICT', message: 'stale', retryable: false } }, 409)
  await assert.rejects(api.replaceQuality('SUP-0151', 0, { defect_ppm: 0, on_time_rate: 0.9, audit_grade: 'A', years_dealing: 0 }),
    error => error.status === 409 && error.code === 'VERSION_CONFLICT')
} finally {
  globalThis.fetch = originalFetch
}

// 등록 폼 → 요청: 사업자번호 원문은 보내지 않고, 백분율은 비율로, 빈 품질은 null로 보낸다.
const register = form.emptyRegisterForm()
Object.assign(register.profile, { name: ' 한빛정밀 ', region: '경남', foundedYear: '2004', bizNo: '123-45-67890', employees: '1,800' })
register.capabilities[0] = { process: '다이캐스팅', material: 'ADC12', monthlyCapacity: '40,000' }
assert.deepEqual(form.validateRegister(register, 2026, '2026-10-23'), {})
const body = form.createRequest(register)
assert.deepEqual(body.profile, {
  name: '한빛정밀', region: '경남', employees: 1800, revenue_eok: null, founded_year: 2004,
  biz_no_masked: '123-45-*****', stock_code: null, induty_code: null, source: 'MANUAL', corp_code: null,
})
assert.equal(body.quality, null)
assert.deepEqual(body.capabilities, [{ process: '다이캐스팅', material: 'ADC12', monthly_capacity: 40000 }])

Object.assign(register.quality, { defectPpm: '450', onTimeRatePct: '97.5', auditGrade: 'A', yearsDealing: '0' })
assert.deepEqual(form.createRequest(register).quality, { defect_ppm: 450, on_time_rate: 0.975, audit_grade: 'A', years_dealing: 0 })
register.quality.onTimeRatePct = '97.55'
assert.ok(form.validateRegister(register, 2026, '2026-10-23').onTimeRatePct, 'server rejects more than three ratio digits')
register.quality = form.emptyQuality()
register.quality.defectPpm = '10'
assert.ok(form.validateRegister(register, 2026, '2026-10-23').auditGrade, 'partly filled quality needs all four values')

register.quality = form.emptyQuality()
register.source = 'DART'
assert.ok(form.validateRegister(register, 2026, '2026-10-23').corpCode)
register.corpCode = '00126380'
assert.equal(form.createRequest(register).profile.corp_code, '00126380')
register.profile.region = '세종'
assert.equal(form.validateRegister(register, 2026, '2026-10-23').region, undefined, 'all 17 server regions are allowed')

register.quotes = [{ process: '다이캐스팅', material: 'ADC12', unitPrice: '3,950', quotedAt: '2026-10-24' }]
assert.ok(form.validateRegister(register, 2026, '2026-10-23')['quote.0.quotedAt'], 'future quote dates are rejected')

// 상세 수정: MOCK 기업이 출처가 바뀌지 않도록 바뀐 필드만 보낸다.
const draft = form.profileDraft({ ...supplier, employees: 12 })
assert.deepEqual(form.changedProfile(draft, supplier), {})
draft.employees = '20'
assert.deepEqual(form.changedProfile(draft, supplier), { employees: 20 })
assert.equal(form.qualityDraft({ defect_ppm: 0, on_time_rate: 0.934, audit_grade: 'B', years_dealing: 0 }).onTimeRatePct, '93.4')

console.log('Admin supplier API checks passed: filters, pagination, target identity, CSRF, version, partial profile changes, quotes, status, conflicts and form conversion.')
