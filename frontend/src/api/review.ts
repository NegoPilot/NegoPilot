import { request } from './client'
import { parseReviewResponse } from './conversation-contract'

export type Tri = '' | 'true' | 'false'
export const tri = (v: Tri): boolean | null => v === '' ? null : v === 'true'
export const numberOrNull = (v: string | number): number | null => String(v).trim() === '' ? null : Number(v)
export function newCompany() {
  return { ksic_code: '', average_revenue_eok: '', assets_eok: '', independence_met: '' as Tri,
    has_related_enterprises: '' as Tri, related_aggregate_revenue_eok: '', related_calculation_verified: false,
    seat_manufacturing: '' as Tri, transition_reviewed: false, deemed_sme: '' as Tri, evidence_reference: '' }
}
export function companyInput(c: ReturnType<typeof newCompany>) {
  return { ...c, ksic_code: c.ksic_code.trim() || null, evidence_reference: c.evidence_reference.trim() || null,
    average_revenue_eok: numberOrNull(c.average_revenue_eok), assets_eok: numberOrNull(c.assets_eok),
    independence_met: tri(c.independence_met), has_related_enterprises: tri(c.has_related_enterprises),
    related_aggregate_revenue_eok: c.has_related_enterprises === 'true' ? numberOrNull(c.related_aggregate_revenue_eok) : null,
    related_calculation_verified: c.has_related_enterprises === 'true' && c.related_calculation_verified,
    seat_manufacturing: tri(c.seat_manufacturing), deemed_sme: tri(c.deemed_sme) }
}
export async function reviewOffer(offer: unknown) {
  return parseReviewResponse(await request('/api/legal-reviews', { method: 'POST', body: JSON.stringify(offer) }, 330_000))
}
