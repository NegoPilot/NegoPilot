import type { LocationQuery, LocationQueryValue } from 'vue-router'

import type { SortDirection, SupplierSearchParams, SupplierSort } from '@/api/types'
import { parsePositiveInteger } from './format'

export interface SearchFormValues {
  q: string
  process: string
  material: string
  minMonthlyCapacity: string
  region: string
  iatfRequired: boolean
  iso14001Required: boolean
  page: number
  size: number
  sort: SupplierSort
  direction: SortDirection
}

export const SUPPLIER_SORTS: { value: SupplierSort; label: string }[] = [
  { value: 'name', label: '협력사명' },
  { value: 'region', label: '지역' },
  { value: 'founded_year', label: '설립연도' },
  { value: 'monthly_capacity', label: '월 생산능력' },
  { value: 'defect_ppm', label: '불량률' },
  { value: 'on_time_delivery_rate', label: '납기 준수율' },
  { value: 'audit_grade', label: '품질감사등급' },
  { value: 'years_dealing', label: '거래기간' },
  { value: 'payment_days', label: '대금 지급기한' },
  { value: 'raw_material_ratio', label: '원재료 비중' },
  { value: 'latest_quote_date', label: '최근 견적일' },
  { value: 'latest_quote_price', label: '최근 견적 단가' },
]
export const PAGE_SIZES = [20, 50, 100] as const

export function emptySearchValues(): SearchFormValues {
  return { q: '', process: '', material: '', minMonthlyCapacity: '', region: '', iatfRequired: false, iso14001Required: false,
    page: 0, size: 20, sort: 'name', direction: 'asc' }
}

function first(value: LocationQueryValue | LocationQueryValue[] | undefined): string {
  const v = Array.isArray(value) ? value[0] : value
  return typeof v === 'string' ? v : ''
}

function nonnegativeInteger(value: string, fallback: number): number {
  const number = Number(value)
  return /^\d+$/.test(value) && Number.isSafeInteger(number) && number >= 0 ? number : fallback
}

/** 필터와 페이지 및 정렬 조건을 URL에 보관한다. API와 URL의 page는 0부터 시작한다. */
export function searchValuesFromQuery(query: LocationQuery): SearchFormValues {
  const values: SearchFormValues = {
    q: first(query.q),
    process: first(query.process),
    material: first(query.material),
    minMonthlyCapacity: first(query.min_capacity),
    region: first(query.region),
    iatfRequired: first(query.iatf) === '1',
    iso14001Required: first(query.iso) === '1',
    page: nonnegativeInteger(first(query.page), 0),
    size: nonnegativeInteger(first(query.size), 20),
    sort: SUPPLIER_SORTS.find((item) => item.value === first(query.sort))?.value ?? 'name',
    direction: first(query.direction) === 'desc' ? 'desc' : 'asc',
  }
  if (!(PAGE_SIZES as readonly number[]).includes(values.size)) values.size = 20
  return safePriceSort(values)
}

/** 서로 다른 공정 및 소재의 단가를 직접 비교하지 않는다. */
export function safePriceSort(values: SearchFormValues): SearchFormValues {
  return values.sort === 'latest_quote_price' && (!values.process.trim() || !values.material.trim())
    ? { ...values, sort: 'name' } : values
}

export function searchValuesToQuery(input: SearchFormValues): Record<string, string> {
  const values = safePriceSort(input)
  const query: Record<string, string> = {}
  const capacity = parseCapacity(values.minMonthlyCapacity)
  if (values.q.trim()) query.q = values.q.trim()
  if (values.process.trim()) query.process = values.process.trim()
  if (values.material.trim()) query.material = values.material.trim()
  if (capacity !== null) query.min_capacity = String(capacity)
  if (values.region.trim()) query.region = values.region.trim()
  if (values.iatfRequired) query.iatf = '1'
  if (values.iso14001Required) query.iso = '1'
  if (values.page > 0) query.page = String(values.page)
  if (values.size !== 20) query.size = String(values.size)
  if (values.sort !== 'name') query.sort = values.sort
  if (values.direction !== 'asc') query.direction = values.direction
  return query
}

/** 공정과 소재는 각각 선택 조건이다. 입력된 생산능력이 잘못된 경우에만 요청하지 않는다. */
export function searchValuesToParams(input: SearchFormValues): SupplierSearchParams | null {
  const values = safePriceSort(input)
  const params: SupplierSearchParams = { page: values.page, size: values.size, sort: values.sort, direction: values.direction }
  if (values.q.trim()) params.q = values.q.trim()
  if (values.process.trim()) params.process = values.process.trim()
  if (values.material.trim()) params.material = values.material.trim()
  if (values.region.trim()) params.region = values.region.trim()
  if (values.iatfRequired) params.iatf_required = true
  if (values.iso14001Required) params.iso14001_required = true
  if (values.minMonthlyCapacity.trim()) {
    const capacity = parseCapacity(values.minMonthlyCapacity)
    if (capacity === null) return null
    params.min_monthly_capacity = capacity
  }
  return params
}

/** 표시할 페이지는 0 기반 인덱스다. 시작과 끝 및 현재 페이지 주변을 노출한다. */
export function pageNumbers(page: number, totalPages: number): (number | 'gap')[] {
  if (totalPages <= 0) return []
  const center = Math.min(Math.max(page, 0), totalPages - 1)
  const candidates = new Set([0, totalPages - 1])
  const start = Math.max(0, Math.min(center - 2, totalPages - 5))
  for (let i = start; i < Math.min(totalPages, start + 5); i++) candidates.add(i)
  const result: (number | 'gap')[] = []
  let previous = -1
  for (const value of [...candidates].sort((a, b) => a - b)) {
    if (previous >= 0 && value - previous > 1) result.push('gap')
    result.push(value)
    previous = value
  }
  return result
}

export const TEXT_MAX = 100
export const CAPACITY_MAX = 10_000_000

export function parseCapacity(raw: string): number | null {
  const value = parsePositiveInteger(raw)
  return value !== null && value <= CAPACITY_MAX ? value : null
}
