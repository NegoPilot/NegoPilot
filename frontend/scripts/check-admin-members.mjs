import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// Exercise the API module and shared HTTP client together without a server or browser.
async function moduleUrl(path, replacements = []) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8')
  for (const [before, after] of replacements) source = source.replaceAll(before, after)
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const clientUrl = await moduleUrl('../src/api/client.ts', [['import.meta.env.VITE_API_TIMEOUT_MS', 'undefined']])
const api = await import(await moduleUrl('../src/api/admin-members.ts', [
  ["from './client'", `from '${clientUrl}'`],
  ['from "./client"', `from '${clientUrl}'`],
]))

const root = '/api/admin/members'
const id = '56f51af3-50dc-42e2-a603-f7cf637df417'
const otherId = '52a3f43d-cc53-4e9f-a17d-0da24fa2af3a'
const stamp = '2026-10-21T05:00:00Z'
const member = {
  id, email: 'member@example.test', name: '테스트 회원', department: '구매팀', job_title: '',
  role: 'USER', status: 'PENDING', created_at: stamp, updated_at: stamp,
}
const page = { items: [member], total: 1, page: 0, size: 20 }
const invalidResponse = error => error.status === 502 && error.code === 'INVALID_RESPONSE'
const invalidRequest = error => error.status === 400 && error.code === 'VALIDATION_ERROR'
const forbidden = error => error.status === 403 && error.code === 'FORBIDDEN' && error.retryable === false
const calls = []
const originalFetch = globalThis.fetch
let respond
function serve(body, status = 200) {
  calls.length = 0
  respond = async () => Response.json(body, { status })
}
function serveUpdate(user) {
  calls.length = 0
  respond = async path => path === '/api/auth/csrf'
    ? Response.json({ header_name: 'X-CSRF-TOKEN', token: 'test-csrf' })
    : Response.json({ user })
}
function queryOf(call) {
  const url = new URL(call.path, 'https://negopilot.example.test')
  assert.equal(url.pathname, root)
  return Object.fromEntries(url.searchParams)
}

try {
  globalThis.fetch = async (path, init) => {
    calls.push({ path, init })
    return respond(path, init)
  }

  // The server returns a page directly. Its member fields use snake_case and uppercase enums.
  serve(page)
  assert.deepEqual(await api.listMembers(), page)
  assert.equal(calls.length, 1, 'GET must not obtain a CSRF token')
  assert.deepEqual(queryOf(calls[0]), { page: '0', size: '20' })
  assert.equal(calls[0].init.method ?? 'GET', 'GET')
  assert.equal(calls[0].init.credentials, 'same-origin')
  assert.equal(calls[0].init.headers.get('Accept'), 'application/json')

  serve({ ...page, total: 5, page: 2, size: 2 })
  const filtered = await api.listMembers({ search: '  김 & ops@example.test  ', role: 'USER', status: 'PENDING', page: 2, size: 2 })
  assert.deepEqual(queryOf(calls[0]), {
    page: '2', size: '2', search: '김 & ops@example.test', role: 'USER', status: 'PENDING',
  })
  assert.deepEqual([filtered.total, filtered.page, filtered.size], [5, 2, 2])
  serve({ items: [], total: 0, page: 100_000, size: 100 })
  assert.deepEqual(await api.listMembers({ search: '   ', page: 100_000, size: 100 }), { items: [], total: 0, page: 100_000, size: 100 })
  assert.deepEqual(queryOf(calls[0]), { page: '100000', size: '100' })

  const rows = ['PENDING', 'ACTIVE', 'REJECTED', 'DISABLED'].map((status, i) => ({
    ...member, id: `00000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}`,
    role: i % 2 ? 'ADMIN' : 'USER', status,
  }))
  serve({ ...page, items: rows, total: 4 })
  assert.deepEqual((await api.listMembers()).items, rows)
  // Count and row reads are separate backend queries. A concurrent signup can change the count.
  serve({ ...page, total: 0 })
  assert.equal((await api.listMembers()).items.length, 1)
  serve({ ...page, items: [{ ...member, internal_note: 'not a public member field' }] })
  assert.deepEqual((await api.listMembers()).items[0], member)

  for (const [field, value] of [
    ['id', 'not-a-uuid'], ['email', null], ['name', 42], ['department', []], ['job_title', null],
    ['role', 'lead'], ['status', 'active'], ['created_at', 'not-a-date'], ['updated_at', null],
  ]) {
    serve({ ...page, items: [{ ...member, [field]: value }] })
    await assert.rejects(api.listMembers(), invalidResponse, `Invalid member ${field} must be rejected`)
  }
  for (const change of [
    { items: null }, { items: [null] }, { total: '1' }, { total: -1 }, { total: 0.5 },
    { total: Number.MAX_SAFE_INTEGER + 1 }, { page: 1 }, { page: '0' }, { size: 10 },
    { size: 0 }, { items: [member, { ...member, id: id.toUpperCase() }] },
    { items: Array.from({ length: 21 }, () => member) },
  ]) {
    serve({ ...page, ...change })
    await assert.rejects(api.listMembers(), invalidResponse, 'Invalid or mismatched server page must be rejected')
  }
  for (const options of [
    { page: -1 }, { page: 100_001 }, { page: 0.5 }, { size: 0 }, { size: 101 },
    { role: 'lead' }, { status: 'active' }, { search: 'x'.repeat(255) }, { search: 'a\0b' },
  ]) {
    serve(page)
    await assert.rejects(api.listMembers(options), invalidRequest)
    assert.equal(calls.length, 0, 'Invalid filter must not be sent to the server')
  }

  // Each approval or rejection obtains the current CSRF token and sends the UUID in the path.
  calls.length = 0
  let tokenCount = 0
  respond = async (path, init) => {
    if (path === '/api/auth/csrf') {
      return Response.json({ header_name: 'X-CSRF-TOKEN', token: `test-csrf-${++tokenCount}` })
    }
    return Response.json({ user: { ...member, status: JSON.parse(init.body).status } })
  }
  for (const status of ['ACTIVE', 'REJECTED']) {
    assert.deepEqual(await api.updateMemberStatus(id, status), { ...member, status })
  }
  assert.equal(calls.length, 4)
  for (const [index, status] of ['ACTIVE', 'REJECTED'].entries()) {
    const csrf = calls[index * 2]
    const patch = calls[index * 2 + 1]
    assert.equal(csrf.path, '/api/auth/csrf')
    assert.equal(csrf.init.credentials, 'same-origin')
    assert.equal(patch.path, `${root}/${id}/status`)
    assert.equal(patch.init.method, 'PATCH')
    assert.equal(patch.init.credentials, 'same-origin')
    assert.equal(patch.init.headers.get('Content-Type'), 'application/json')
    assert.equal(patch.init.headers.get('X-CSRF-TOKEN'), `test-csrf-${index + 1}`)
    assert.deepEqual(JSON.parse(patch.init.body), { status })
  }
  for (const user of [
    { ...member, id: otherId, status: 'ACTIVE' },
    { ...member, status: 'REJECTED' },
    { ...member, status: 'ACTIVE', role: 'staff' },
    null,
  ]) {
    serveUpdate(user)
    await assert.rejects(api.updateMemberStatus(id, 'ACTIVE'), invalidResponse, 'Unconfirmed target or status must not count as approval')
  }
  for (const [target, status] of [['../other', 'ACTIVE'], [id, 'approved']]) {
    serveUpdate(member)
    await assert.rejects(api.updateMemberStatus(target, status), invalidRequest)
    assert.equal(calls.length, 0)
  }

  const denied = { request_id: null, error: { code: 'FORBIDDEN', message: 'Access is denied.', retryable: false } }
  serve(denied, 403)
  await assert.rejects(api.listMembers(), forbidden)
  serve(denied, 403)
  await assert.rejects(api.updateMemberStatus(id, 'ACTIVE'), forbidden)
  assert.deepEqual(calls.map(call => call.path), ['/api/auth/csrf'], 'CSRF failure must prevent PATCH')
  calls.length = 0
  respond = async path => path === '/api/auth/csrf'
    ? Response.json({ header_name: 'X-CSRF-TOKEN', token: 'test-csrf' })
    : Response.json(denied, { status: 403 })
  await assert.rejects(api.updateMemberStatus(id, 'REJECTED'), forbidden)
  assert.equal(calls.length, 2)

  // Aborting an obsolete list request must remain distinguishable from a failed server response.
  respond = async (_path, init) => new Promise((_, reject) => {
    const abort = () => reject(new DOMException('Aborted', 'AbortError'))
    if (init.signal.aborted) abort()
    else init.signal.addEventListener('abort', abort, { once: true })
  })
  const abort = new AbortController()
  const cancelled = api.listMembers({}, abort.signal)
  abort.abort()
  await assert.rejects(cancelled, error => error.code === 'REQUEST_CANCELLED')
} finally {
  globalThis.fetch = originalFetch
}

console.log('Admin member API checks passed: filters, pagination, server fields, enums, target identity, approval/rejection, CSRF, cookies, forbidden responses and cancellation.')
