import type { LocationQuery, LocationQueryValue } from 'vue-router'

import type { SupplierSearchParams } from '@/api/types'
import { parsePositiveInteger } from './format'

/** 폼에 입력된 그대로의 값. 최소 월 생산능력도 입력 문자열로 둔다 */
export interface SearchFormValues {
  process: string
  material: string
  minMonthlyCapacity: string
  region: string
  iatfRequired: boolean
  iso14001Required: boolean
}

export function emptySearchValues(): SearchFormValues {
  return { process: '', material: '', minMonthlyCapacity: '', region: '', iatfRequired: false, iso14001Required: false }
}

function first(value: LocationQueryValue | LocationQueryValue[] | undefined): string {
  const v = Array.isArray(value) ? value[0] : value
  return typeof v === 'string' ? v : ''
}

/** 검색 조건은 URL query에 둔다. 새로고침·뒤로 가기에도 조건이 남는다 */
export function searchValuesFromQuery(query: LocationQuery): SearchFormValues {
  return {
    process: first(query.process),
    material: first(query.material),
    minMonthlyCapacity: first(query.min_capacity),
    region: first(query.region),
    iatfRequired: first(query.iatf) === '1',
    iso14001Required: first(query.iso) === '1',
  }
}

export function searchValuesToQuery(values: SearchFormValues): Record<string, string> {
  const query: Record<string, string> = {}
  const process = values.process.trim()
  const material = values.material.trim()
  const capacity = parsePositiveInteger(values.minMonthlyCapacity)
  if (process) query.process = process
  if (material) query.material = material
  if (capacity !== null) query.min_capacity = String(capacity)
  if (values.region) query.region = values.region
  if (values.iatfRequired) query.iatf = '1'
  if (values.iso14001Required) query.iso = '1'
  return query
}

/** 보낼 수 없는 조건(공정 없음, 생산능력이 숫자가 아님)이면 null */
export function searchValuesToParams(values: SearchFormValues): SupplierSearchParams | null {
  const process = values.process.trim()
  if (!process) return null
  const params: SupplierSearchParams = { process }
  const material = values.material.trim()
  if (material) params.material = material
  if (values.region) params.region = values.region
  if (values.iatfRequired) params.iatf_required = true
  if (values.iso14001Required) params.iso14001_required = true
  if (values.minMonthlyCapacity.trim()) {
    const capacity = parsePositiveInteger(values.minMonthlyCapacity)
    if (capacity === null) return null
    params.min_monthly_capacity = capacity
  }
  return params
}

export const TEXT_MAX = 100
