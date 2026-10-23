import { API_PATHS, request } from './client'
import type { Catalog, Envelope, QuoteSearchParams, SupplierDetail, SupplierList, SupplierQuoteList, SupplierSearchParams } from './types'

export async function getCatalog(): Promise<Catalog> {
  const res = await request<Envelope<Catalog>>(API_PATHS.businessCatalog)
  return res.data
}

export async function searchSuppliers(params: SupplierSearchParams): Promise<SupplierList> {
  const query = new URLSearchParams({ page: String(params.page), size: String(params.size), sort: params.sort, direction: params.direction })
  if (params.q) query.set('q', params.q)
  if (params.process) query.set('process', params.process)
  if (params.material) query.set('material', params.material)
  if (params.region) query.set('region', params.region)
  if (params.iatf_required) query.set('iatf_required', 'true')
  if (params.iso14001_required) query.set('iso14001_required', 'true')
  if (params.min_monthly_capacity !== undefined) query.set('min_monthly_capacity', String(params.min_monthly_capacity))
  const res = await request<Envelope<SupplierList>>(`${API_PATHS.suppliers}?${query.toString()}`)
  return res.data
}

export async function getSupplier(supplierId: string): Promise<SupplierDetail> {
  const res = await request<Envelope<SupplierDetail>>(`${API_PATHS.suppliers}/${encodeURIComponent(supplierId)}`)
  return res.data
}

export async function getSupplierQuotes(supplierId: string, params: QuoteSearchParams): Promise<SupplierQuoteList> {
  const query = new URLSearchParams({ page: String(params.page), size: String(params.size), sort: params.sort, direction: params.direction })
  if (params.process) query.set('process', params.process)
  if (params.material) query.set('material', params.material)
  const res = await request<Envelope<SupplierQuoteList>>(`${API_PATHS.suppliers}/${encodeURIComponent(supplierId)}/quotes?${query.toString()}`)
  return res.data
}
