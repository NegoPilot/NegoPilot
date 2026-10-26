// 08-2 법규 검토 + 협상안 화면의 API.
// 기본은 Spring 협상 작업 API(live)를 부른다. VITE_OFFER_API=mock 이면 피그마 예시 데이터로만 돈다.
// 형식은 docs/contracts/negotiation-workspace.md(작업·검토·협상안·내보내기·메일), offer-document.md, mail.md 를 따른다.
import { API_PATHS, ApiError, request } from './client'
import type { Member } from './auth'

const LIVE = import.meta.env.VITE_OFFER_API !== 'mock'

// ── ② 법규 검토 ──

export type ReviewVerdict = 'approved' | 'rejected'

export interface ReviewRow {
  id: string
  verdict: ReviewVerdict
  item: string
  result: string
  basis: string
  /** 반려 사유. 승인이면 null */
  reason: string | null
  action: { label: string; url: string | null }
}

/** 엑셀에서 고른 협력사의 행 하나. 같은 협력사 행이 RFQ별로 여러 개면 화면에서 고른다 */
export interface OfferUploadRow { row_number: number; rfq_id: string | null; status: string }

export interface OfferUpload {
  upload_id: string
  supplier_name: string
  file_name: string
  rows: OfferUploadRow[]
}

export interface OfferReview {
  review_id: string
  supplier_name: string
  file_name: string
  row_number: number
  rows: ReviewRow[]
  /** 어느 항목에 붙일 수 없는 반려 사유(조건 검사 오류, 생성 불가 판정). 있으면 협상안을 만들 수 없다 */
  notice: string | null
}

// ── ③ 협상안 ──

export interface OfferSender { company: string; department: string; contact_name: string; job_title: string; phone: string | null; email: string }

/** 협력사 발송용 제안서 (offer-document.md 1절에서 화면이 쓰는 필드) */
export interface OfferDocument {
  schema_version: '1'
  document_id: string
  version: number
  status: 'draft' | 'final'
  issued_on: string
  as_of: string
  reply_by: string
  supplier_id: string
  supplier_name: string
  sender: OfferSender
  title: string
  rfq_id: string
  part_name: string
  vehicle: string | null
  material: string | null
  annual_volume: number
  unit_price: number
  contract_days: number
  contract_amount: number
  payment_days: number
  payment_method: string
  price_linkage: boolean
  linkage_waived_by_agreement: boolean
  linkage_needs_input: boolean
  raw_material_ratio: number | null
  energy_ratio: number | null
  delivery_notes: string | null
  cover_letter: string
  verdict_code: 'OK' | 'CHECK_NEEDED' | 'VIOLATION' | null
  content_hash: string | null
}

/** 내부 협상 브리핑 중 화면에서 보여 주고 고치는 부분 (offer-proposal.md 4·6절) */
export interface InternalBriefing {
  concessions: string[]
  objections: Array<{ issue: string; response: string }>
  strategy: string
  opening_message: string
}

export interface OfferWarning { code: string; message: string }

export interface OfferProposal {
  document: OfferDocument
  briefing: InternalBriefing
  check: { verdict_code: OfferDocument['verdict_code']; warnings: OfferWarning[] }
}

export interface RenderedOffer {
  document: OfferDocument
  attachments: Array<{ filename: string; content_type: string; data_base64: string }>
  check: OfferProposal['check']
}

// ── ④ 메일 ──

export interface MailPreview {
  to: { name: string; email: string; supplier_name: string }
  from: string
  /** 협력사가 답장하면 가는 주소. mail-mcp가 제안서 발신 담당자로 넣고, 없으면 null (mail.md 2절) */
  reply_to?: string | null
  subject: string
  body: string
  attachments: Array<{ filename: string; content_type: string; bytes: number }>
  expires_at: string
  /** mock만 쓴다. live는 확인 토큰을 서버에만 둔다 */
  confirmation_token?: string
  /** live: 서버의 메일 미리보기 id와 내용 해시 */
  mail_id?: string
  content_hash?: string
}

export interface MailSendResult { status: 'SENT' | 'DRY_RUN'; to: string; subject: string; sent_at: string }

export type FileFormat = 'pdf' | 'docx'

// ── 공개 함수 ──

/** ① 엑셀을 올리고 이 회사의 행 목록을 받는다. 행이 여러 개면 화면에서 하나를 고른다 */
export function uploadOfferExcel(supplierName: string, file: File, signal?: AbortSignal): Promise<OfferUpload> {
  return LIVE ? live.upload(supplierName, file, signal) : mock.upload(supplierName, file, signal)
}

/** ② 고른 행의 검토가 끝날 때까지 기다려 항목별 승인·반려를 받는다 */
export function loadOfferReview(upload: OfferUpload, rowNumber: number, signal?: AbortSignal): Promise<OfferReview> {
  return LIVE ? live.load(upload, rowNumber, signal) : mock.load(upload, rowNumber, signal)
}

/** ② 모든 항목이 승인된 검토로 협상안을 만든다. 발신 정보는 서버가 로그인 회원으로 채운다 */
export function createOfferProposal(review: OfferReview, member: Member | null): Promise<OfferProposal> {
  return LIVE ? live.create(review) : mock.create(review, member)
}

/** ③ 고친 제안서를 다시 검사하고 확정본(final)과 첨부를 받는다. 위반이면 422 OFFER_VIOLATION */
export function renderOfferDocument(document: OfferDocument, files: FileFormat | 'both' = 'both', briefing?: InternalBriefing): Promise<RenderedOffer> {
  return LIVE ? live.render(document, files, briefing) : mock.render(document, files)
}

// 메일에는 협력사 제안서 PDF만 붙인다. 본문은 mail-mcp가 정한 문장이고, 담당자는 가운데에 들어갈 추가 문구(message)만 쓴다.
// 확인 토큰이 본문까지 서명하므로, 추가 문구를 쓰면 그 문구로 미리보기를 다시 받고 그 토큰으로 보낸다(mail.md 4절).
export const MAX_MAIL_MESSAGE = 2000
export function previewOfferMail(rendered: RenderedOffer, message?: string): Promise<MailPreview> {
  return LIVE ? live.preview(rendered, message) : mock.preview(rendered, message)
}

export function sendOfferMail(rendered: RenderedOffer, preview: MailPreview, message?: string): Promise<MailSendResult> {
  return LIVE ? live.send(rendered, preview, message) : mock.send(preview)
}

/** 미리보기 본문을 추가 문구 앞뒤로 나눈다. mail-mcp compose는 추가 문구 다음에 "검토하신 뒤 … 회신 부탁드립니다." 줄을 둔다 */
export function splitMailBody(body: string): { head: string; tail: string } {
  const at = body.lastIndexOf('\n\n검토하신 뒤')
  return at < 0 ? { head: body, tail: '' } : { head: body.slice(0, at), tail: body.slice(at + 2) }
}

/** 확정본 첨부에서 파일을 꺼내 브라우저로 저장한다 */
export function saveAttachment(rendered: RenderedOffer, format: FileFormat) {
  const file = rendered.attachments.find(a => a.filename.endsWith(`.${format}`))
  if (!file) throw new ApiError(0, 'ATTACHMENT_MISSING', 'Attachment not found.', false)
  const bytes = Uint8Array.from(atob(file.data_base64), c => c.charCodeAt(0))
  const url = URL.createObjectURL(new Blob([bytes], { type: file.content_type }))
  const link = Object.assign(document.createElement('a'), { href: url, download: file.filename })
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

// ── live: Spring 협상 작업 API (docs/contracts/negotiation-workspace.md) ──
// 화면의 함수 모양은 그대로 두고, 서버가 저장한 작업·검토·협상안·내보내기·메일 식별자는 여기서 들고 다닌다.

type Json = Record<string, any>
const idempotent = () => ({ 'Idempotency-Key': crypto.randomUUID() })
const SLOW = 330_000
const POLL_MS = 2_000
const nego = (id: string) => `${API_PATHS.negotiations}/${encodeURIComponent(id)}`
const uploadPath = (upload: OfferUpload) => `${API_PATHS.legalReviewUploads}/${encodeURIComponent(upload.upload_id)}`

interface ReviewRef { negotiationId: string; revision: number; summaries: Record<string, string> }
interface ProposalRef {
  negotiationId: string; proposalId: string; head: number; summaries: Record<string, string>
  base: { document: OfferDocument; briefing: InternalBriefing }
  unresolved: string[]; checkNeeded: boolean
}
const reviewRefs = new Map<string, ReviewRef>()      // key: OfferReview.review_id (= 협상 작업 id)
const proposalRefs = new Map<string, ProposalRef>()  // key: OfferDocument.document_id (버전이 바뀌어도 같다)

function proposalRef(document: OfferDocument) {
  const ref = proposalRefs.get(document.document_id)
  if (!ref) throw new ApiError(409, 'PROPOSAL_NOT_LOADED', 'Proposal is not loaded.', false)
  return ref
}
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value))

const live = {
  // ① 엑셀을 #284 업로드 API로 올리고 이 회사의 행 목록을 돌려준다. 검토는 행마다 서버에서 이어서 돈다
  async upload(supplierName: string, file: File, signal?: AbortSignal): Promise<OfferUpload> {
    if (!/\.xlsx$/i.test(file.name)) throw new ApiError(400, 'NOT_XLSX', '엑셀(.xlsx) 파일만 불러올 수 있습니다.', false)
    const supplierId = await resolveSupplier(supplierName, signal)
    const form = new FormData()
    form.append('file', file)
    // 이 회사의 행만 검토를 요청한다. 다른 회사 행은 SKIPPED로 남고 Agent를 부르지 않는다
    form.append('supplier_id', supplierId)
    const upload = await request<Json>(API_PATHS.legalReviewUploads, { method: 'POST', body: form, signal }, SLOW)
    const mine: Json[] = (upload.rows ?? []).filter((r: Json) => r.supplier_id === supplierId)
    if (!mine.length) {
      throw new ApiError(422, 'SUPPLIER_NOT_IN_FILE', `엑셀에 ${supplierName}(${supplierId}) 행이 없습니다. 공급자 ID 열을 확인해 주세요.`, false)
    }
    return { upload_id: String(upload.upload_id), supplier_name: supplierName, file_name: file.name,
      rows: mine.map(r => ({ row_number: Number(r.row_number), rfq_id: r.rfq_id ?? null, status: String(r.status) })) }
  },

  // ② 고른 행의 검토가 끝날 때까지 기다린 뒤 ② 표로 바꾼다. signal이 끊기면(화면 이탈, 새 업로드) 조회를 멈춘다
  async load(upload: OfferUpload, rowNumber: number, signal?: AbortSignal): Promise<OfferReview> {
    const rowPath = `${uploadPath(upload)}/rows/${rowNumber}`
    let row = await request<Json>(rowPath, { signal })
    const deadline = Date.now() + SLOW
    while (row.status === 'QUEUED' || row.status === 'RUNNING') {
      if (Date.now() > deadline) throw new ApiError(504, 'REVIEW_TIMEOUT', 'Legal review is taking too long.', true)
      await wait(POLL_MS, signal)
      row = await request<Json>(rowPath, { signal })
    }
    if (row.status === 'INVALID') {
      const found = (row.input_errors ?? []).map((e: Json) => `${e.column}: ${e.message}`).join(' / ')
      throw new ApiError(400, 'ROW_INVALID', `엑셀 ${row.row_number}행 입력 오류 · ${found}`, false)
    }
    if (row.status === 'FAILED') throw new ApiError(502, 'REVIEW_FAILED', String(row.error?.code ?? 'Legal review failed.'), !!row.error?.retryable)
    const id = String(row.negotiation_id)
    const revision = Number(row.review_revision)
    const head = Number(row.review_head_revision ?? revision)
    // 협상안은 최신 검토 버전으로만 만들 수 있다. 업로드 뒤 재검토가 쌓였으면 표도 그 버전으로 그려 둘을 맞춘다
    const stored: Json = head > revision ? await request<Json>(`${nego(id)}/reviews/${head}`, { signal }) : row.response ?? {}
    const eligibility = head > revision ? stored.eligibility : row.eligibility ?? stored.eligibility
    const findings: Json[] = stored.legal_review?.assessment?.findings ?? []
    const summaries = Object.fromEntries(findings.map(f => [String(f.id), String(f.summary ?? f.id)]))
    reviewRefs.set(id, { negotiationId: id, revision: head, summaries })
    const { rows, notice } = reviewView(stored, eligibility)
    const label = `${upload.file_name} · ${rowNumber}행${head > revision ? ` · 재검토 v${head} 기준` : ''}`
    return { review_id: id, supplier_name: upload.supplier_name, file_name: label, row_number: rowNumber, rows, notice }
  },

  async create(review: OfferReview): Promise<OfferProposal> {
    const ref = reviewRefs.get(review.review_id)
    if (!ref) throw new ApiError(409, 'REVIEW_NOT_LOADED', 'Review is not loaded.', false)
    const view = await request<Json>(`${nego(ref.negotiationId)}/proposals`, { method: 'POST', headers: idempotent(),
      body: JSON.stringify({ review_revision: ref.revision, offer: {} }) }, SLOW)
    if (view.proposal?.status !== 'offer') {
      const reasons: string[] = view.proposal?.no_offer_reasons ?? []
      throw new ApiError(422, 'NO_OFFER', reasons.join(' / ') || 'No offer.', false)
    }
    const external = await request<Json>(`${nego(ref.negotiationId)}/proposals/${encodeURIComponent(view.proposal_id)}/external`)
    const document = external.external_document as OfferDocument
    const briefing = briefingFrom(view.proposal)
    proposalRefs.set(document.document_id, {
      negotiationId: ref.negotiationId, proposalId: String(view.proposal_id), head: Number(view.head_revision), summaries: ref.summaries,
      base: { document: clone(document), briefing: clone(briefing) },
      unresolved: view.validation?.unresolved_issue_ids ?? [], checkNeeded: view.proposal?.offer?.verdict_code === 'CHECK_NEEDED',
    })
    return { document, briefing, check: { verdict_code: view.proposal.offer.verdict_code ?? null, warnings: warningsOf(view.proposal.offer.warnings) } }
  },

  // [제안서 확정]: 고친 값을 새 버전으로 저장 → 검사가 끝나지 않았으면 재검사 → 확정본 Word·PDF를 만든다
  async render(document: OfferDocument, _files: FileFormat | 'both', briefing?: InternalBriefing): Promise<RenderedOffer> {
    const ref = proposalRef(document)
    const path = `${nego(ref.negotiationId)}/proposals/${encodeURIComponent(ref.proposalId)}`
    const changes = changesOf(ref.base, document, briefing ?? ref.base.briefing)
    let view: Json = Object.keys(changes).length
      ? await request<Json>(path, { method: 'PATCH', headers: idempotent(), body: JSON.stringify({ base_revision: ref.head, changes }) })
      : await request<Json>(path)
    ref.head = Number(view.head_revision)
    let validation: Json = view.validation
    if (validation.status !== 'checked') validation = await recheck(ref, path)
    if (validation.status !== 'checked') throw new ApiError(503, 'VALIDATION_FAILED', 'Validation did not finish.', true)
    if (validation.conclusion === 'risk_identified') {
      const found = (validation.violations ?? []).map((v: Json) => v.message ?? v.code).join(' / ')
      throw new ApiError(422, 'OFFER_VIOLATION', found || 'Offer violates a rule.', false)
    }
    let bundle: Json
    try { bundle = await exportFinal(ref, ['docx', 'pdf']) }
    catch (e) {
      // 검사는 끝났지만 인사말이 조건과 맞지 않는 버전(예: #306 이전에 검사한 버전)은 재검사로 인사말을 맞춘다
      if (!(e instanceof ApiError && e.code === 'EXPORT_NOT_ALLOWED'
        && (e.details?.reasons as string[] | undefined)?.includes('COVER_LETTER_STALE'))) throw e
      await recheck(ref, path)
      throw e
    }
    const attachments = await Promise.all((bundle.files as Json[]).map(async file => ({
      filename: String(file.file_name), content_type: String(file.content_type), data_base64: await downloadBase64(String(file.download_url)),
    })))
    const external = await request<Json>(`${path}/external`)
    const final = { ...(external.external_document as OfferDocument), status: 'final' as const, content_hash: String(bundle.content_hash) }
    view = await request<Json>(path)
    ref.base = { document: clone(final), briefing: clone(briefingFrom(view.proposal)) }
    ref.unresolved = validation.unresolved_issue_ids ?? []
    ref.checkNeeded = bundle.render_check?.verdict_code === 'CHECK_NEEDED'
    const warnings = [...warningsOf(bundle.render_check?.warnings),
      ...ref.unresolved.map(id => ({ code: id, message: `추가 확인 필요: ${ref.summaries[id] ?? id}` }))]
    return { document: final, attachments, check: { verdict_code: bundle.render_check?.verdict_code ?? final.verdict_code, warnings } }
  },

  // 메일에는 PDF만 붙이므로 PDF 하나짜리 확정본으로 미리보기를 받는다.
  // 확인 체크(경고·미확인 사항)는 화면 창에서 받는다. 실제 발송은 체크 뒤 send에서 미리보기를 새로 받아 그것으로만 보낸다.
  async preview(rendered: RenderedOffer, message?: string): Promise<MailPreview> {
    const ref = proposalRef(rendered.document)
    const bundle = await exportFinal(ref, ['pdf'])
    const mail = await request<Json>(`${nego(ref.negotiationId)}/proposals/${encodeURIComponent(ref.proposalId)}/mail-previews`, {
      method: 'POST', headers: idempotent(), body: JSON.stringify({ bundle_id: bundle.bundle_id, ...(message ? { message } : {}),
        acknowledgements: { check_needed: ref.checkNeeded, unresolved_issue_ids: ref.unresolved } }) }, SLOW)
    return {
      to: { name: mail.to.name, email: mail.to.email, supplier_name: mail.to.supplier_name ?? rendered.document.supplier_name },
      from: decodeHeader(String(mail.from)), reply_to: typeof mail.reply_to === 'string' ? decodeHeader(mail.reply_to) : null,
      subject: mail.subject, body: mail.body, attachments: mail.attachments,
      expires_at: mail.expires_at, mail_id: mail.mail_id, content_hash: mail.content_hash,
    }
  },

  async send(rendered: RenderedOffer, _preview: MailPreview, message?: string): Promise<MailSendResult> {
    const fresh = await live.preview(rendered, message)
    const sent = await request<Json>(`${API_PATHS.negotiationMails}/${encodeURIComponent(fresh.mail_id!)}/send`, {
      method: 'POST', headers: idempotent(), body: JSON.stringify({ content_hash: fresh.content_hash }) }, SLOW)
    if (sent.status === 'FAILED') throw new ApiError(422, String(sent.error_code ?? 'MAIL_SEND_FAILED'), 'Mail was not sent.', false)
    if (sent.status === 'UNKNOWN') throw new ApiError(202, 'MAIL_RESULT_UNKNOWN', 'Mail result is unknown.', false)
    return { status: sent.status, to: String(sent.to?.email ?? '').replace(/^(..).*@/, '$1***@'), subject: sent.subject,
      sent_at: sent.finished_at ?? new Date().toISOString() }
  },
}

// 재검사한다. 고친 조건과 맞지 않아 서버가 인사말을 다시 써서 새 버전으로 저장했으면
// 바뀐 인사말을 보여 주고 다시 확정받도록 COVER_LETTER_REFRESHED를 던진다 (#306)
async function recheck(ref: ProposalRef, path: string): Promise<Json> {
  const checked = await request<Json>(`${path}/revisions/${ref.head}/validations`, { method: 'POST', headers: idempotent(), body: '{}' }, SLOW)
  const validation: Json = checked.validation
  if (checked.cover_letter?.refreshed && validation.status === 'checked' && validation.conclusion !== 'risk_identified') {
    ref.head = Number(checked.cover_letter.revision)
    const external = await request<Json>(`${path}/external`)
    const refreshed = external.external_document as OfferDocument
    ref.base = { ...ref.base, document: clone(refreshed) }
    throw new ApiError(409, 'COVER_LETTER_REFRESHED', 'The cover letter was rewritten.', false, null, { document: refreshed })
  }
  return validation
}

async function exportFinal(ref: ProposalRef, formats: FileFormat[]) {
  const bundle = await request<Json>(`${nego(ref.negotiationId)}/proposals/${encodeURIComponent(ref.proposalId)}/exports`, {
    method: 'POST', headers: idempotent(), body: JSON.stringify({ revision: ref.head, document_type: 'external', formats, final: true }) }, SLOW)
  if (bundle.status !== 'ready') throw new ApiError(503, 'EXPORT_NOT_READY', 'Export is not ready.', true)
  return bundle
}

async function downloadBase64(url: string) {
  if (!url.startsWith('/api/negotiation-exports/')) throw new ApiError(502, 'INVALID_RESPONSE', 'Invalid download url.', false)
  const response = await fetch(url, { credentials: 'same-origin' })
  if (!response.ok) throw new ApiError(response.status, `HTTP_${response.status}`, 'Download failed.', response.status >= 500)
  const bytes = new Uint8Array(await response.arrayBuffer())
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
}

// 화면에서 고친 값을 PATCH changes로 바꾼다. 공통 조건은 offer, 제안서 문구는 external, 브리핑 문구는 writing·offer
function changesOf(base: ProposalRef['base'], document: OfferDocument, briefing: InternalBriefing) {
  const changes: Json = {}
  const put = (section: string, key: string, before: unknown, after: unknown) => {
    if (JSON.stringify(before) !== JSON.stringify(after)) (changes[section] ??= {})[key] = after
  }
  for (const key of ['unit_price', 'payment_days', 'contract_days', 'price_linkage'] as const) put('offer', key, base.document[key], document[key])
  put('offer', 'notes', base.document.delivery_notes, document.delivery_notes)
  put('offer', 'concessions', base.briefing.concessions, briefing.concessions.map(c => c.trim()).filter(Boolean))
  put('offer', 'objections', base.briefing.objections, briefing.objections)
  for (const key of ['title', 'cover_letter', 'reply_by'] as const) put('external', key, base.document[key], document[key])
  put('writing', 'strategy', base.briefing.strategy, briefing.strategy)
  put('writing', 'opening_message', base.briefing.opening_message, briefing.opening_message)
  return changes
}

// ② 표: 저장된 법규 검토(Agent)의 쟁점 판단과 check_offer 검사 항목을 승인/반려 두 가지로 보여 준다.
// 반려는 생성이 막히는 판단(blocked)이나 위반 소지(violation)만이다. 추가 확인이 필요한 항목은 승인으로 두고 결과에 적는다.
const LAW_URL = 'https://www.law.go.kr/법령/하도급거래공정화에관한법률'
const ROWS: Array<{ id: string; item: string; finding: string | null; codes: RegExp; basis: string; article: string | null }> = [
  { id: 'payment', item: '대금 지급기한', finding: 'payment.deadline', codes: /^PAYMENT_|^MIDSIZE_PAYMENT/, basis: '하도급법 제13조', article: '제13조' },
  { id: 'linkage', item: '납품대금 연동 약정', finding: 'linkage.agreement', codes: /LINKAGE|RAW_MATERIAL_RATIO|^ENERGY_/, basis: '하도급법 제3조', article: '제3조' },
  { id: 'price_cut', item: '단가 인하 폭', finding: 'price.scope', codes: /^PRICE_CUT|^ABOVE_TARGET/, basis: '하도급법 제4조', article: '제4조' },
  { id: 'size', item: '구매자·협력사 규모', finding: 'transaction.applicability', codes: /^SME_|^BUYER_SIZE|^SUBCONTRACT_ACT/, basis: '하도급법 제2조', article: '제2조' },
  { id: 'duration', item: '계약 기간·물량', finding: 'duration.commitment', codes: /^$/, basis: '하도급법 제3조·제8조', article: '제8조' },
  { id: 'market', item: '시장가 하한', finding: null, codes: /^PRICE_BELOW_MARKET/, basis: 'rules.py', article: null },
  { id: 'capability', item: '생산능력 / 인증', finding: null, codes: /^CERT_|^CAPABILITY_|^CAPACITY_/, basis: 'business-mcp', article: null },
]
const PASS: Record<string, string> = { market: '시장가 하한 이상', capability: 'RFQ 생산능력·인증 조건 충족' }

function reviewRows(stored: Json): ReviewRow[] {
  const findings: Json[] = stored.legal_review?.assessment?.findings ?? []
  // TRANSACTION_* 항목은 위 쟁점 판단(findings)을 옮긴 것이라 겹쳐 쓰지 않는다
  const evidence: Json[] = (stored.answer?.evidence ?? []).filter((e: Json) => e.kind === 'offer_finding' && !String(e.meta?.code ?? '').startsWith('TRANSACTION_'))
  const checked = (stored.answer?.evidence ?? []).some((e: Json) => e.kind === 'offer_verdict')
  return ROWS.map(row => {
    const finding = row.finding ? findings.find(f => f.id === row.finding) : undefined
    const codes = evidence.filter(e => row.codes.test(String(e.meta?.code ?? '')))
    const violations = codes.filter(e => e.meta?.severity === 'violation')
    const rejected = finding?.status === 'blocked' || violations.length > 0
    const plain = (e: Json) => String(e.text ?? '').replace(/^\[[^\]]+\]\s*/, '').replace(/\s*관련 조문:.*$/, '')
    const texts = [finding?.summary, ...codes.map(plain)]
      .filter((t): t is string => !!t && t.trim().length > 0)
    const missing: string[] = finding?.missing_fields ?? []
    const result = texts.join(' · ') || (checked ? PASS[row.id] ?? '검사 범위 내 문제 없음' : '검사 결과 없음')
    const url = finding?.law_refs?.[0]?.url ?? (row.article ? `${LAW_URL}/${row.article}` : null)
    return {
      id: row.id, verdict: rejected ? 'rejected' : 'approved', item: row.item,
      result: missing.length ? `${result} (추가 확인 ${missing.length}건)` : result,
      basis: row.basis, reason: rejected ? (violations.map(plain).join(' / ') || finding?.summary || '조건을 고쳐 다시 검토해 주세요.') : null,
      action: url ? { label: '원문 보기 ↗', url } : { label: '정보 조회', url: null },
    }
  })
}

// check_offer가 조건 자체를 거절한 오류 코드마다 검사하지 못한 항목. 없는 코드(VALUE_OUT_OF_RANGE 등)는 표 위 안내로 보여 준다
const CHECK_ERRORS: Record<string, { rows: string[]; reason: string }> = {
  RFQ_NOT_FOUND: { rows: ['market', 'capability'], reason: 'RFQ를 찾지 못해 검사하지 못했습니다. 엑셀의 RFQ ID를 확인해 주세요.' },
  SUPPLIER_NOT_FOUND: { rows: ['size', 'capability'], reason: '협력사를 찾지 못해 검사하지 못했습니다. 엑셀의 공급자 ID를 확인해 주세요.' },
}

// 행 요약의 check_error_code와 같은 규칙(LegalReviewRowView.checkError)으로, 화면에 그리는 검토에서 직접 읽는다
function checkOfferError(stored: Json): string | null {
  for (const outcome of stored.routing?.outcomes ?? []) {
    for (const call of outcome.calls ?? []) if (call.tool === 'check_offer' && typeof call.error_code === 'string') return call.error_code
  }
  return null
}

// 표와 표 위 안내. 조건 검사 오류는 그 오류로 검사하지 못한 항목에 반려로 붙이고, 붙일 항목이 없거나
// 서버 판정만 생성 불가(blocked)이고 반려 항목이 없으면 다른 행을 빌리지 않고 안내로 둔다
function reviewView(stored: Json, eligibility: unknown): { rows: ReviewRow[]; notice: string | null } {
  const code = checkOfferError(stored)
  const known = code ? CHECK_ERRORS[code] : undefined
  const rows = reviewRows(stored).map(row => known?.rows.includes(row.id)
    ? { ...row, verdict: 'rejected' as const, result: `검사하지 못함 (${code})`, reason: known.reason } : row)
  const notice = code && !known
    ? code === 'VALUE_OUT_OF_RANGE'
      ? '조건을 검사하지 못했습니다 (VALUE_OUT_OF_RANGE). 단가·일수는 0보다 크게, 비중은 0~1, 날짜는 YYYY-MM-DD로 고친 엑셀을 다시 불러와 주세요.'
      : `조건을 검사하지 못했습니다 (${code}). 엑셀의 공급자 ID·RFQ ID·값 범위를 확인해 주세요.`
    : eligibility === 'blocked' && !rows.some(r => r.verdict === 'rejected')
      ? '법규 검토에서 협상안 생성이 막혔습니다. 항목별 결과와 추가 확인 사항을 보고 조건을 고친 엑셀을 다시 불러와 주세요.'
      : null
  return { rows, notice }
}

const warningsOf = (raw: unknown): OfferWarning[] =>
  Array.isArray(raw) ? raw.map((w: Json) => ({ code: String(w.code ?? ''), message: String(w.message ?? w.code ?? '') })) : []

function briefingFrom(raw: Record<string, unknown>): InternalBriefing {
  const offer = (raw.offer ?? {}) as { concessions?: string[]; objections?: InternalBriefing['objections'] }
  const writing = (raw.writing ?? {}) as { strategy?: string; opening_message?: string }
  return { concessions: offer.concessions ?? [], objections: offer.objections ?? [],
    strategy: writing.strategy ?? '', opening_message: writing.opening_message ?? '' }
}

// 메일 헤더의 =?utf-8?b?…?= 를 읽을 수 있게 바꾼다
function decodeHeader(value: string) {
  return value.replace(/=\?utf-8\?b\?([^?]+)\?=/gi, (_, b64: string) => new TextDecoder().decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0))))
}

async function resolveSupplier(name: string, signal?: AbortSignal) {
  const list = await request<{ data: { items: Array<{ supplier_id: string; name: string }> } }>(
    `${API_PATHS.suppliers}?${new URLSearchParams({ q: name, limit: '10' })}`, { signal })
  const exact = list.data.items.filter(s => s.name === name)
  const found = exact.length === 1 ? exact[0] : list.data.items.length === 1 ? list.data.items[0] : null
  if (!found) throw new ApiError(422, 'SUPPLIER_AMBIGUOUS', exact.length > 1 || list.data.items.length > 1
    ? '같은 이름의 협력사가 여러 곳입니다. 정확한 회사명을 입력해 주세요.' : '협력사를 찾을 수 없습니다.', false)
  return found.supplier_id
}

/** 서버 오류 코드를 화면 문구로 바꾼다 */
export function describeOfferError(error: ApiError): string | null {
  const reasons = Array.isArray(error.details?.reasons) ? (error.details!.reasons as string[]) : []
  const REASONS: Record<string, string> = {
    BUNDLE_NOT_FINAL: '확정본이 아닙니다', BUNDLE_STALE: '확정 이후 조건이 바뀌었습니다', PROPOSAL_SUPERSEDED: '새 협상안이 만들어졌습니다',
    REVIEW_RECHECK_REQUIRED: '법규 검토를 다시 해야 합니다', VALIDATION_REQUIRED: '제안서를 다시 확정해 주세요', RISK_IDENTIFIED: '위반 소지가 있습니다',
    COVER_LETTER_STALE: '인사말이 지금 조건과 맞지 않습니다. 제안서를 다시 확정해 주세요',
    OFFER_VIOLATION: '위반 소지가 있습니다', CHECK_NEEDED_NOT_ACKNOWLEDGED: '확인 필요 항목을 체크해 주세요', UNRESOLVED_NOT_ACKNOWLEDGED: '확인 필요 항목을 체크해 주세요',
  }
  const known: Record<string, string> = {
    SEND_NOT_ALLOWED: `발송할 수 없습니다: ${[...new Set(reasons.map(r => REASONS[r] ?? r))].join(' / ')}`,
    EXPORT_NOT_ALLOWED: `확정할 수 없습니다: ${[...new Set(reasons.map(r => REASONS[r] ?? r))].join(' / ')}`,
    COVER_LETTER_REFRESHED: '고친 조건에 맞게 인사말을 바꿨습니다. 인사말을 확인한 뒤 [제안서 확정]을 다시 눌러 주세요.',
    MESSAGE_INTERNAL_INFO: '추가 문구에 내부 협상 정보(대외비·목표 합의가·최대 수용가·내부 가격 등)가 있습니다.',
    ALREADY_SENT: '같은 제안서 버전을 이 담당자에게 이미 보냈습니다.',
    PREVIEW_EXPIRED: '미리보기 확인 시간(15분)이 지났습니다. 미리보기를 다시 열어 주세요.',
    PREVIEW_STALE: '미리보기 이후 제안서가 바뀌었습니다. 미리보기를 다시 열어 주세요.',
    RECIPIENT_MISSING: '협력사 담당자가 등록되어 있지 않습니다.', SENDER_INCOMPLETE: '발신자 정보(회원 이름·부서·이메일)가 비어 있습니다.',
    MAIL_SEND_FAILED: '메일 서버가 발송을 거절했습니다. 잠시 뒤 다시 시도해 주세요.',
    MAIL_RESULT_UNKNOWN: '발송 결과를 아직 알 수 없습니다. 잠시 뒤 발송 기록을 확인해 주세요. 자동으로 다시 보내지 않습니다.',
    REVISION_CONFLICT: '다른 곳에서 먼저 고쳤습니다. 화면을 새로 고친 뒤 다시 수정해 주세요.',
    NOT_XLSX: error.message, SUPPLIER_AMBIGUOUS: error.message, SUPPLIER_NOT_IN_FILE: error.message, ROW_INVALID: error.message,
    REVIEW_TIMEOUT: '법규 검토가 오래 걸리고 있습니다. 잠시 뒤 다시 불러와 주세요.', NO_OFFER: `협상안을 만들 수 없습니다: ${error.message}`,    // 업로드 한도 초과는 Spring과 nginx 모두 413 CONTENT_TOO_LARGE다. JSON이 아닌 413(HTTP_413)도 같은 안내로 본다
    CONTENT_TOO_LARGE: '파일이 1MB를 넘습니다. 1MB 이하로 줄여 다시 올려 주세요.',
    HTTP_413: '파일이 1MB를 넘습니다. 1MB 이하로 줄여 다시 올려 주세요.',
    TOO_MANY_PENDING_REVIEWS: '앞서 올린 엑셀의 검토가 아직 진행 중입니다. 검토가 끝난 뒤 다시 올려 주세요.',
    VALIDATION_ERROR: Array.isArray(error.details?.errors)
      ? `엑셀 형식 오류 · ${(error.details!.errors as Array<{ column?: string; message?: string }>).map(e => [e.column, e.message].filter(Boolean).join(': ')).join(' / ')}`
      : '입력값을 확인해 주세요.',
  }
  return known[error.code] ?? null
}

// ── mock: 피그마 08-2 시안 값 ──

// signal이 끊기면 request와 같은 REQUEST_CANCELLED로 멈춘다
const wait = (ms: number, signal?: AbortSignal) => new Promise<void>((resolve, reject) => {
  const cancel = () => { clearTimeout(timer); reject(new ApiError(0, 'REQUEST_CANCELLED', 'Request was cancelled.', false)) }
  const timer = setTimeout(() => { signal?.removeEventListener('abort', cancel); resolve() }, ms)
  if (signal?.aborted) cancel()
  else signal?.addEventListener('abort', cancel, { once: true })
})
const today = () => new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' })
function addDays(date: string, days: number) {
  const d = new Date(`${date}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10)
}
const LAW = (article: string) => `https://www.law.go.kr/법령/하도급거래공정화에관한법률/${article}`

const mock = {
  async upload(supplierName: string, file: File, signal?: AbortSignal): Promise<OfferUpload> {
    await wait(300, signal)
    // 파일 이름에 "여러"가 있으면 행 고르기를 확인할 수 있게 같은 협력사 행을 두 개로 본다
    const rows = [{ row_number: 2, rfq_id: 'RFQ-2026-001', status: 'COMPLETED' }]
    if (/여러|multi/i.test(file.name)) rows.push({ row_number: 3, rfq_id: 'RFQ-2026-002', status: 'COMPLETED' })
    return { upload_id: `mock-${Date.now()}`, supplier_name: supplierName, file_name: file.name, rows }
  },

  async load(upload: OfferUpload, rowNumber: number, signal?: AbortSignal): Promise<OfferReview> {
    await wait(600, signal)
    // 파일 이름에 "반려"가 있으면 반려 화면을 확인할 수 있게 지급기한을 75일로 본다
    const reject = /반려|reject/i.test(upload.file_name)
    const rows: ReviewRow[] = [
      reject
        ? { id: 'payment', verdict: 'rejected', item: '대금 지급기한', result: '75일 · 수령일부터 60일 초과', basis: '하도급법 제13조①',
            reason: '지급기한을 60일 이내로 고쳐 엑셀을 다시 불러오세요.', action: { label: '원문 보기 ↗', url: LAW('제13조') } }
        : { id: 'payment', verdict: 'approved', item: '대금 지급기한', result: '60일 · 60일 이내 지급', basis: '하도급법 제13조', reason: null, action: { label: '원문 보기 ↗', url: LAW('제13조') } },
      { id: 'linkage', verdict: 'approved', item: '납품대금 연동 약정', result: '원재료 비중 40% (10% 이상) · 연동 약정 포함', basis: '하도급법 제3조', reason: null, action: { label: '원문 보기 ↗', url: LAW('제3조') } },
      { id: 'price_cut', verdict: 'approved', item: '단가 인하 폭', result: '3,800원 · 직전 견적 4,100원 대비 -7.3%', basis: '하도급법 제4조', reason: null, action: { label: '근거 확인 ↗', url: LAW('제4조') } },
      { id: 'size', verdict: 'approved', item: '구매자·협력사 규모', result: '하도급법 적용 대상 확인', basis: '하도급법 제2조', reason: null, action: { label: '원문 보기 ↗', url: LAW('제2조') } },
      { id: 'market', verdict: 'approved', item: '시장가 하한', result: '시장 중앙값 4,050원의 93.8% (하한 90%)', basis: 'rules.py', reason: null, action: { label: '정보 조회', url: null } },
      { id: 'capability', verdict: 'approved', item: '생산능력 / 인증', result: '월 25,000 EA · IATF 16949 · RFQ 필수조건 충족', basis: 'business-mcp', reason: null, action: { label: '정보 조회', url: null } },
    ]
    return { review_id: `mock-${Date.now()}`, supplier_name: upload.supplier_name, file_name: `${upload.file_name} · ${rowNumber}행`,
      row_number: rowNumber, rows, notice: null }
  },

  async create(review: OfferReview, member: Member | null): Promise<OfferProposal> {
    await wait(800)
    const issued = today()
    const document: OfferDocument = {
      schema_version: '1', document_id: `NP-RFQ-2026-001-SUP-0034-${issued.replaceAll('-', '')}`, version: 1, status: 'draft',
      issued_on: issued, as_of: issued, reply_by: addDays(issued, 7),
      supplier_id: 'SUP-0034', supplier_name: review.supplier_name,
      sender: { company: 'SKALA 자동차', department: member?.department ?? '구매1팀', contact_name: member?.name ?? '김가연',
        job_title: member?.job_title ?? '매니저', phone: null, email: member?.email ?? 'buyer@negopilot.local' },
      title: '단가 협의 제안서', rfq_id: 'RFQ-2026-001', part_name: '알루미늄 브래킷', vehicle: null, material: 'ADC12',
      annual_volume: 120_000, unit_price: 3800, contract_days: 365, contract_amount: 456_000_000, payment_days: 60, payment_method: '현금',
      price_linkage: true, linkage_waived_by_agreement: false, linkage_needs_input: false, raw_material_ratio: 0.4, energy_ratio: null,
      delivery_notes: '거래 기간: 12개월 · 주요 원재료 ADC12 시세에 연동, 분기별 단가 조정',
      cover_letter: '귀사의 협력에 감사드립니다. RFQ-2026-001 알루미늄 브래킷 단가와 거래 조건을 아래와 같이 제안드리오니 검토 후 회신 부탁드립니다.',
      verdict_code: 'OK', content_hash: null,
    }
    const briefing: InternalBriefing = {
      concessions: ['① 거래 기간 연장 (12 → 24개월)', '② 단가 3,900원까지 (목표 합의가)', '③ 최대 4,000원 · 팀장 승인 필요'],
      objections: [
        { issue: '원재료 시세 상승분 반영 요구', response: '연동 약정으로 대응' },
        { issue: '지급기한 단축 요구 (60일 → 30일)', response: '단가 양보 대신 지급 단축을 검토' },
      ],
      strategy: '시장 중앙값보다 낮은 3,800원으로 시작해 연동 약정을 근거로 단가를 지킨다.',
      opening_message: '연동 약정을 넣었기 때문에 ADC12 시세가 오르면 분기마다 단가에 반영됩니다. 그래서 기본 단가는 3,800원으로 제안드립니다.',
    }
    return { document, briefing, check: { verdict_code: 'OK', warnings: [] } }
  },

  async render(document: OfferDocument, files: FileFormat | 'both'): Promise<RenderedOffer> {
    await wait(500)
    // render API처럼 고친 조건을 다시 검사한다 (check_offer의 지급기한 규칙만 흉내)
    if (document.payment_days > 60) throw new ApiError(422, 'OFFER_VIOLATION', '지급기한 60일 초과', false)
    const final: OfferDocument = { ...document, status: 'final', contract_amount: document.unit_price * document.annual_volume,
      content_hash: `sha256:mock${document.version}` }
    const formats: FileFormat[] = files === 'both' ? ['pdf', 'docx'] : [files]
    const text = mockDocumentText(final)
    const attachments = formats.map(format => ({
      filename: `제안서_${final.rfq_id}_${final.supplier_id}_v${final.version}${format === 'pdf' ? '.pdf' : '.docx'}`,
      content_type: format === 'pdf' ? 'application/pdf' : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      // mock 파일은 실제 PDF·DOCX가 아니라 본문 텍스트다. 실제 파일은 Agent render가 만든다
      data_base64: btoa(String.fromCharCode(...new TextEncoder().encode(text))),
    }))
    return { document: final, attachments, check: { verdict_code: final.verdict_code, warnings: [] } }
  },

  async preview(rendered: RenderedOffer, message?: string): Promise<MailPreview> {
    await wait(400)
    const d = rendered.document
    const attachment = rendered.attachments.find(a => a.filename.endsWith('.pdf'))!
    return {
      to: { name: '김영업', email: `sales.${d.supplier_id.toLowerCase()}@example.test`, supplier_name: d.supplier_name },
      from: 'NegoPilot 구매팀 <purchasing@negopilot.local>', reply_to: `${d.sender.contact_name} <${d.sender.email}>`,
      ...mockCompose(d, '김영업', message),
      attachments: [{ filename: attachment.filename, content_type: attachment.content_type, bytes: atob(attachment.data_base64).length }],
      confirmation_token: `mock-token-${Date.now()}`, expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    }
  },

  async send(preview: MailPreview): Promise<MailSendResult> {
    await wait(700)
    return { status: 'DRY_RUN', to: preview.to.email.replace(/^(..).*@/, '$1***@'), subject: preview.subject, sent_at: new Date().toISOString() }
  },
}

// mail-mcp offer_mail.compose와 같은 문장 (본문에는 단가·지급 조건 숫자를 쓰지 않는다)
function mockCompose(d: OfferDocument, contactName: string, message?: string) {
  const about = d.title.includes(d.rfq_id) ? d.title : `${d.rfq_id} ${d.part_name} 건 ${d.title}`
  const lines = [`${d.supplier_name} ${contactName}님께`, '', `안녕하십니까. ${d.sender.company} ${d.sender.department}입니다.`,
    `${about} 문서를 첨부와 같이 보내 드립니다.`]
  if (message?.trim()) lines.push('', message.trim())
  lines.push('', `검토하신 뒤 ${d.reply_by}까지 회신 부탁드립니다.`, '', '감사합니다.',
    `${d.sender.company} ${d.sender.department} ${d.sender.contact_name}`, d.sender.email, '', `(제안서 번호 ${d.document_id} · 버전 ${d.version})`)
  return { subject: `[${d.sender.company}] ${about}`, body: lines.join('\n') }
}

function mockDocumentText(d: OfferDocument) {
  return [`${d.title} (mock 파일)`, `수신: ${d.supplier_name} (${d.supplier_id})`,
    `발신: ${d.sender.company} · ${d.sender.department} ${d.sender.contact_name} ${d.sender.job_title}`, '',
    d.cover_letter, '', `대상 품목: ${d.rfq_id} ${d.part_name}, 연간 ${d.annual_volume} EA`, `제안 단가: ${d.unit_price}원 / EA`,
    `대금 지급: 납품 후 ${d.payment_days}일 이내 ${d.payment_method} 지급`, `거래 기간: ${d.contract_days}일`,
    `납품대금 연동: ${d.price_linkage ? '포함' : '없음'}`, d.delivery_notes ?? '', `회신 요청일: ${d.reply_by}`].join('\n')
}
