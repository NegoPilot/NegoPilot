// 08-2 법규 검토 + 협상안 화면의 API.
// Spring 프록시(협상안 생성·렌더, 메일 미리보기·발송)는 아직 main에 없다. 그 전까지는 mock으로 화면을 돌리고,
// VITE_OFFER_API=live 로 켜면 아래 live 함수가 Spring 공개 API를 부른다.
// 형식은 docs/contracts/offer-proposal.md, offer-document.md, mail.md, check-offer.md 를 따른다.
import { ApiError, request } from './client'
import type { Member } from './auth'

const LIVE = import.meta.env.VITE_OFFER_API === 'live'
// TODO(백엔드): 경로는 Spring 프록시가 정해지면 client.ts의 API_PATHS로 옮긴다.
const OFFER_PATH = '/api/offer-proposals'
const MAIL_PATH = '/api/offer-mails'

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

export interface OfferReview {
  review_id: string
  supplier_name: string
  file_name: string
  rows: ReviewRow[]
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
  subject: string
  body: string
  attachments: Array<{ filename: string; content_type: string; bytes: number }>
  confirmation_token: string
  expires_at: string
}

export interface MailSendResult { status: 'SENT' | 'DRY_RUN'; to: string; subject: string; sent_at: string }

export type FileFormat = 'pdf' | 'docx'

// ── 공개 함수 ──

/** ① 엑셀을 올려 ② 항목별 승인·반려를 받는다 */
export function reviewOfferExcel(supplierName: string, file: File): Promise<OfferReview> {
  return LIVE ? live.review(supplierName, file) : mock.review(supplierName, file)
}

/** ② 모든 항목이 승인된 검토로 협상안을 만든다. 발신 정보는 서버가 로그인 회원으로 채운다 */
export function createOfferProposal(review: OfferReview, member: Member | null): Promise<OfferProposal> {
  return LIVE ? live.create(review) : mock.create(review, member)
}

/** ③ 고친 제안서를 다시 검사하고 확정본(final)과 첨부를 받는다. 위반이면 422 OFFER_VIOLATION */
export function renderOfferDocument(document: OfferDocument, files: FileFormat | 'both' = 'both'): Promise<RenderedOffer> {
  return LIVE ? live.render(document, files) : mock.render(document, files)
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

// ── live: Spring 프록시가 생기면 쓴다 ──

const live = {
  // TODO(백엔드): 엑셀 → 항목별 check_offer 결과를 주는 API가 아직 없다. Agent /batch는 zip만 돌려준다.
  async review(_supplierName: string, _file: File): Promise<OfferReview> {
    throw new ApiError(501, 'NOT_IMPLEMENTED', 'Excel review API is not ready.', false)
  },
  async create(review: OfferReview): Promise<OfferProposal> {
    const body = await request<{ offer_document: OfferDocument | null; check: OfferProposal['check']; internal_briefing: Record<string, unknown> }>(
      OFFER_PATH, { method: 'POST', body: JSON.stringify({ review_id: review.review_id }) }, 330_000)
    if (!body.offer_document) throw new ApiError(422, 'NO_OFFER', 'No offer.', false)
    return { document: body.offer_document, check: body.check, briefing: briefingFrom(body.internal_briefing) }
  },
  render(document: OfferDocument, files: FileFormat | 'both') {
    return request<RenderedOffer>(`${OFFER_PATH}/render`, { method: 'POST', body: JSON.stringify({ document, files }) }, 120_000)
  },
  // TODO(#264): Spring 메일 API가 생기면 경로와 요청 형식을 맞춘다. 첨부는 확정본 그대로 보낸다(해시 검사).
  preview(rendered: RenderedOffer, message?: string) {
    return request<MailPreview>(`${MAIL_PATH}/preview`, { method: 'POST', body: JSON.stringify(mailInput(rendered, message)) })
  },
  send(rendered: RenderedOffer, preview: MailPreview, message?: string) {
    return request<MailSendResult>(`${MAIL_PATH}/send`, { method: 'POST',
      body: JSON.stringify({ ...mailInput(rendered, message), confirmation_token: preview.confirmation_token }) })
  },
}

function mailInput(rendered: RenderedOffer, message?: string) {
  return { document: rendered.document, attachments: rendered.attachments.filter(a => a.filename.endsWith('.pdf')),
    ...(message ? { message } : {}) }
}

function briefingFrom(raw: Record<string, unknown>): InternalBriefing {
  const offer = (raw.offer ?? {}) as { concessions?: string[]; objections?: InternalBriefing['objections'] }
  const writing = (raw.writing ?? {}) as { strategy?: string; opening_message?: string }
  return { concessions: offer.concessions ?? [], objections: offer.objections ?? [],
    strategy: writing.strategy ?? '', opening_message: writing.opening_message ?? '' }
}

// ── mock: 피그마 08-2 시안 값 ──

const wait = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))
const today = () => new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' })
function addDays(date: string, days: number) {
  const d = new Date(`${date}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10)
}
const LAW = (article: string) => `https://www.law.go.kr/법령/하도급거래공정화에관한법률/${article}`

const mock = {
  async review(supplierName: string, file: File): Promise<OfferReview> {
    await wait(600)
    // 파일 이름에 "반려"가 있으면 반려 화면을 확인할 수 있게 지급기한을 75일로 본다
    const reject = /반려|reject/i.test(file.name)
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
    return { review_id: `mock-${Date.now()}`, supplier_name: supplierName, file_name: file.name, rows }
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
      from: `${d.sender.contact_name} <${d.sender.email}>`,
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
