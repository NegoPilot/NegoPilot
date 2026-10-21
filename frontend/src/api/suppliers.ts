import { request } from './client'
import type { Catalog, Envelope, SupplierDetail, SupplierList, SupplierSearchParams } from './types'

export async function getCatalog(): Promise<Catalog> {
  const res = await request<Envelope<Catalog>>('/api/v2/business/catalog')
  return res.data
}

export async function searchSuppliers(params: SupplierSearchParams): Promise<SupplierList> {
  const query = new URLSearchParams({ process: params.process })
  if (params.material) query.set('material', params.material)
  if (params.region) query.set('region', params.region)
  // 계약상 false는 '인증 여부 전체'라 체크했을 때만 보낸다
  if (params.iatf_required) query.set('iatf_required', 'true')
  if (params.iso14001_required) query.set('iso14001_required', 'true')
  if (params.min_monthly_capacity !== undefined) query.set('min_monthly_capacity', String(params.min_monthly_capacity))
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  const res = await request<Envelope<SupplierList>>(`/api/v2/suppliers?${query.toString()}`)
  return res.data
}

export async function getSupplier(supplierId: string): Promise<SupplierDetail> {
  const res = await request<Envelope<SupplierDetail>>(`/api/v2/suppliers/${encodeURIComponent(supplierId)}`)
  return res.data
}
