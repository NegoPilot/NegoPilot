import { API_PATHS, ApiError, request } from './client'
import { parseReviewResponse } from './conversation-contract'

// 협상 작업 API (docs/contracts/negotiation-workspace.md). 검토 결과·협상안 버전은 서버에 저장된다.
export type Eligibility = 'ready' | 'conditional' | 'blocked' | null
export type ValidationStatus = 'not_checked' | 'recheck_required' | 'checking' | 'checked' | 'failed'
type Json = Record<string, unknown>

export interface Workspace {
  negotiation_id: string; review_id: string; supplier_id: string; rfq_id: string; title: string; status: string; stage: string
  row_version: number; review_head_revision: number; current_proposal_id: string | null
  eligibility?: Eligibility; current_proposal?: { proposal_id: string; head_revision: number; validation: ValidationState } | null
}
export interface StoredLegalReview {
  review_id: string; version: string; input_hash: string
  assessment: { status: string; findings: Array<{ id: string; summary: string; status: string; missing_fields: string[]; law_refs: Array<{ title: string; url: string; source_url?: string | null }> }>; missing_fields: string[]; constraints: unknown[] }
}
export type StoredReview = ReturnType<typeof parseReviewResponse> & {
  review_id: string; review_revision: number; base_revision: number | null; input_snapshot: Json
  eligibility: Eligibility; created_at: string; legal_review?: StoredLegalReview
}
export interface ValidationState {
  status: ValidationStatus; checked_revision: number | null; conclusion: string | null; inherited_from?: number
  scope: string[]; limitations: string[]; warnings: Array<{ code?: string; message?: string }>; violations: Array<{ code?: string; message?: string }>
  text_findings: unknown; unresolved_issue_ids: string[]; changed_fields?: string[]; error_code?: string; previous?: ValidationState
}
export interface EditEntry { from_revision: number; to_revision: number; edited_by: string; edited_at: string; changes: Array<{ path: string; before: unknown; after: unknown }>; change_note: string | null }
export interface ProposalView {
  proposal_id: string; proposal_revision: number; head_revision: number; status: string
  based_on_review: { review_id: string; review_revision: number }; review_recheck_required: boolean
  proposal: Json; external_document: Json | null; generation_input_snapshot: Json; validation: ValidationState
  edit_history: EditEntry[]; derived_stale: boolean; available_export_formats: { internal: string[]; external: string[] }
  impacts?: { internal?: { content_revision?: number; recheck?: string[]; user_text_unchecked?: boolean }; external?: { content_revision?: number; recheck?: string[] } }
}
export interface ExternalView {
  proposal_id: string; proposal_revision: number; external_document: Json
  validation: { status: ValidationStatus; checked_revision: number | null; conclusion: string | null }
}
export interface ExportFile { file_id: string; format: string; file_name: string; content_type: string; byte_size: number; sha256: string; download_url: string }
export interface ExportBundle {
  bundle_id: string; status: string; final: boolean; proposal_revision: number; content_hash: string | null
  render_check: Json | null; validation_snapshot: { status: ValidationStatus; checked_revision: number | null; conclusion: string | null }; files: ExportFile[]
}
export interface OfferGoals { intent?: 'target' | 'market' | 'payment'; target_price?: number; max_price?: number; notes?: string }

const SLOW = 330_000
const INTERNAL_ONLY = ['price_range', 'concessions', 'objections', 'writing', 'target_price', 'max_price', 'benchmarks', 'considered', 'filled', 'rationale']
const invalid = (what: string) => new ApiError(502, 'INVALID_RESPONSE', `Invalid ${what}.`, false)
const isObject = (v: unknown): v is Json => typeof v === 'object' && v !== null && !Array.isArray(v)
const idempotent = () => ({ 'Idempotency-Key': crypto.randomUUID() })
const base = (id: string) => `${API_PATHS.negotiations}/${encodeURIComponent(id)}`

export function parseWorkspace(v: unknown): Workspace {
  if (!isObject(v) || typeof v.negotiation_id !== 'string' || typeof v.supplier_id !== 'string' || typeof v.rfq_id !== 'string'
    || typeof v.review_head_revision !== 'number' || typeof v.row_version !== 'number') throw invalid('negotiation')
  return v as unknown as Workspace
}
export function parseStoredLegalReview(v: unknown): StoredLegalReview | undefined {
  if (v === undefined || v === null) return undefined
  if (!isObject(v) || 'review_token' in v || typeof v.review_id !== 'string' || !isObject(v.assessment)
    || !Array.isArray(v.assessment.findings) || !Array.isArray(v.assessment.missing_fields)) throw invalid('legal review')
  return v as unknown as StoredLegalReview
}
export function parseStoredReview(v: unknown): StoredReview {
  if (!isObject(v) || 'review_token' in v) throw invalid('review')
  const generation = parseReviewResponse(v)
  if (generation.execution_status === 'FAILED') return { ...generation, review_id: '', review_revision: 0, base_revision: null, input_snapshot: {}, eligibility: null, created_at: '' }
  if (typeof v.review_id !== 'string' || typeof v.review_revision !== 'number' || !isObject(v.input_snapshot)
    || !['ready', 'conditional', 'blocked', null].includes(v.eligibility as string | null)) throw invalid('review')
  return { ...generation, review_id: v.review_id, review_revision: v.review_revision, base_revision: (v.base_revision as number | null) ?? null,
    input_snapshot: v.input_snapshot, eligibility: v.eligibility as Eligibility, created_at: String(v.created_at ?? ''),
    legal_review: parseStoredLegalReview(v.legal_review) }
}
export function parseValidation(v: unknown): ValidationState {
  if (!isObject(v) || !['not_checked', 'recheck_required', 'checking', 'checked', 'failed'].includes(String(v.status))) throw invalid('validation')
  return { scope: [], limitations: [], warnings: [], violations: [], unresolved_issue_ids: [], text_findings: null, checked_revision: null, conclusion: null, ...v } as unknown as ValidationState
}
export function parseProposal(v: unknown): ProposalView {
  if (!isObject(v) || typeof v.proposal_id !== 'string' || typeof v.proposal_revision !== 'number' || typeof v.head_revision !== 'number'
    || !isObject(v.proposal) || !Array.isArray(v.edit_history) || !isObject(v.available_export_formats)) throw invalid('proposal')
  return { ...(v as unknown as ProposalView), validation: parseValidation(v.validation), derived_stale: v.derived_stale === true }
}
export function parseExternal(v: unknown): ExternalView {
  if (!isObject(v) || typeof v.proposal_id !== 'string' || !isObject(v.external_document) || !isObject(v.validation)) throw invalid('external proposal')
  // 외부 응답에 내부 협상 정보가 섞여 오면 화면에 그리지 않는다.
  for (const key of INTERNAL_ONLY) if (key in v || key in v.external_document) throw invalid('external proposal')
  return v as unknown as ExternalView
}
export function parseBundle(v: unknown): ExportBundle {
  if (!isObject(v) || typeof v.bundle_id !== 'string' || !Array.isArray(v.files) || !isObject(v.validation_snapshot)) throw invalid('export')
  return v as unknown as ExportBundle
}

export async function createNegotiation(supplier_id: string, rfq_id: string) {
  return parseWorkspace(await request(API_PATHS.negotiations, { method: 'POST', headers: idempotent(), body: JSON.stringify({ supplier_id, rfq_id }) }))
}
export async function getNegotiation(id: string) { return parseWorkspace(await request(base(id))) }
export async function runReview(id: string, offer: Json, baseRevision: number | null) {
  const body = { ...offer, base_revision: baseRevision }
  return parseStoredReview(await request(`${base(id)}/reviews`, { method: 'POST', headers: idempotent(), body: JSON.stringify(body) }, SLOW))
}
export async function getReview(id: string, revision: number) { return parseStoredReview(await request(`${base(id)}/reviews/${revision}`)) }
export async function createProposal(id: string, reviewRevision: number, offer: OfferGoals) {
  return parseProposal(await request(`${base(id)}/proposals`, { method: 'POST', headers: idempotent(), body: JSON.stringify({ review_revision: reviewRevision, offer }) }, SLOW))
}
export async function getProposal(id: string, pid: string) { return parseProposal(await request(`${base(id)}/proposals/${encodeURIComponent(pid)}`)) }
export async function getExternalProposal(id: string, pid: string) {
  return parseExternal(await request(`${base(id)}/proposals/${encodeURIComponent(pid)}/external`))
}
export async function patchProposal(id: string, pid: string, baseRevision: number, changes: Json, changeNote: string) {
  const body: Json = { base_revision: baseRevision, changes }
  if (changeNote.trim()) body.change_note = changeNote.trim()
  return parseProposal(await request(`${base(id)}/proposals/${encodeURIComponent(pid)}`, { method: 'PATCH', headers: idempotent(), body: JSON.stringify(body) }))
}
export async function runValidation(id: string, pid: string, revision: number) {
  const value = await request<Json>(`${base(id)}/proposals/${encodeURIComponent(pid)}/revisions/${revision}/validations`, { method: 'POST', headers: idempotent(), body: '{}' }, SLOW)
  if (!isObject(value)) throw invalid('validation')
  return { execution: value.execution as Json | undefined, validation: parseValidation(value.validation) }
}
export async function createExport(id: string, pid: string, revision: number, formats: string[]) {
  const body = { revision, document_type: 'external', formats, final: false }
  return parseBundle(await request(`${base(id)}/proposals/${encodeURIComponent(pid)}/exports`, { method: 'POST', headers: idempotent(), body: JSON.stringify(body) }, SLOW))
}
export async function downloadExportFile(file: ExportFile) {
  if (!file.download_url.startsWith(`${API_PATHS.negotiationExports}/`)) throw invalid('download url')
  const response = await fetch(file.download_url, { credentials: 'same-origin' })
  if (!response.ok) throw new ApiError(response.status, `HTTP_${response.status}`, 'Download failed.', response.status >= 500)
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url; link.download = file.file_name
  document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1_000)
}

// 편집 화면 값과 원본을 비교해 바뀐 경로만 PATCH changes로 만든다 (계약 문서 4절 구조).
export interface EditState {
  offer: { unit_price: number; payment_days: number; price_linkage: boolean; contract_days: number; linkage_waived_by_agreement: boolean; notes: string
    settlement: number | null; walk_away: number | null; concessions: string[]; objections: Array<{ issue: string; response: string }> }
  writing: { strategy: string; opening_message: string; objection_scripts: string[] } | null
  external: { title: string; cover_letter: string; reply_by: string; linkage_terms: Record<string, string> } | null
}
export const LINKAGE_KEYS = ['기준 지표', '조정 요건(변동률)', '조정 주기', '조정 산식'] as const
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
export function buildChanges(before: EditState, after: EditState): Json {
  const changes: Json = {}
  const offer: Json = {}
  for (const key of ['unit_price', 'payment_days', 'price_linkage', 'contract_days', 'linkage_waived_by_agreement', 'concessions', 'objections'] as const) {
    if (!same(before.offer[key], after.offer[key])) offer[key] = after.offer[key]
  }
  if (before.offer.notes !== after.offer.notes) offer.notes = after.offer.notes.trim() === '' ? null : after.offer.notes
  const range: Json = {}
  if (before.offer.settlement !== after.offer.settlement && after.offer.settlement !== null) range.settlement = after.offer.settlement
  if (before.offer.walk_away !== after.offer.walk_away && after.offer.walk_away !== null) range.walk_away = after.offer.walk_away
  if (Object.keys(range).length) offer.price_range = range
  if (Object.keys(offer).length) changes.offer = offer
  if (before.writing && after.writing) {
    const writing: Json = {}
    for (const key of ['strategy', 'opening_message', 'objection_scripts'] as const) if (!same(before.writing[key], after.writing[key])) writing[key] = after.writing[key]
    if (Object.keys(writing).length) changes.writing = writing
  }
  if (before.external && after.external) {
    const external: Json = {}
    for (const key of ['title', 'cover_letter', 'reply_by'] as const) if (before.external[key] !== after.external[key]) external[key] = after.external[key]
    if (!same(before.external.linkage_terms, after.external.linkage_terms)) {
      external.linkage_terms = Object.fromEntries(LINKAGE_KEYS.map(k => [k, after.external!.linkage_terms[k] ?? '']))
    }
    if (Object.keys(external).length) changes.external = external
  }
  return changes
}

export function editStateFrom(view: ProposalView, external: ExternalView | null): EditState {
  const offer = (view.proposal.offer ?? {}) as Json
  const range = Array.isArray(offer.price_range) ? offer.price_range as Json[] : []
  const price = (name: string) => { const p = range.find(r => r.name === name); return typeof p?.unit_price === 'number' ? p.unit_price : null }
  const writing = isObject(view.proposal.writing) ? view.proposal.writing : null
  const doc = external?.external_document
  return {
    offer: {
      unit_price: Number(offer.unit_price ?? 0), payment_days: Number(offer.payment_days ?? 0), price_linkage: offer.price_linkage === true,
      contract_days: Number(offer.contract_days ?? 0), linkage_waived_by_agreement: offer.linkage_waived_by_agreement === true,
      notes: typeof offer.notes === 'string' ? offer.notes : '', settlement: price('settlement'), walk_away: price('walk_away'),
      concessions: Array.isArray(offer.concessions) ? (offer.concessions as unknown[]).map(String) : [],
      objections: Array.isArray(offer.objections) ? (offer.objections as Json[]).map(o => ({ issue: String(o.issue ?? ''), response: String(o.response ?? '') })) : [],
    },
    writing: writing ? { strategy: String(writing.strategy ?? ''), opening_message: String(writing.opening_message ?? ''),
      objection_scripts: Array.isArray(writing.objection_scripts) ? (writing.objection_scripts as unknown[]).map(String) : [] } : null,
    external: doc ? { title: String(doc.title ?? ''), cover_letter: String(doc.cover_letter ?? ''), reply_by: String(doc.reply_by ?? ''),
      linkage_terms: Object.fromEntries(LINKAGE_KEYS.map(k => [k, String((doc.linkage_terms as Json | undefined)?.[k] ?? '')])) } : null,
  }
}
