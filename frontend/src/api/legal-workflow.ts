import { ApiError } from './client'
export interface LegalReview {
  review_id: string; version: string; input_hash: string; review_token: string
  assessment: {status: string; findings: Array<{id: string; summary: string; status: string; missing_fields: string[]; law_refs: Array<{title: string; url: string; source_url?: string | null}>}>; missing_fields: string[]; constraints: unknown[]}
}
export function parseLegalReview(value: unknown): LegalReview | undefined {
  if (value === undefined) return undefined
  const r = value as LegalReview
  if (!r || typeof r.review_id !== 'string' || typeof r.review_token !== 'string' || !r.review_token
      || typeof r.input_hash !== 'string' || !r.assessment || !Array.isArray(r.assessment.findings)
      || !Array.isArray(r.assessment.missing_fields)) throw new ApiError(502, 'INVALID_RESPONSE', 'Invalid legal review.', false)
  return r
}
