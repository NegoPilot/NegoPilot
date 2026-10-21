import { reactive } from 'vue'

import { CURRENT_ADMIN } from '@/data/accounts'

// 데이터 등록 요청. 등록 API가 없어서 서버에 저장하지 않고 이 화면 안에만 둔다.
// 담당자가 JSON을 내려받아 seed나 원문 폴더에 반영하는 용도다. 새로고침하면 사라진다.

export interface DataRequest {
  id: string
  kind: 'supplier' | 'law'
  target: string
  summary: string
  requestedAt: string
  requestedBy: string
  payload: unknown
}

const state = reactive({ items: [] as DataRequest[] })

function stamp(date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}.${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function add(kind: DataRequest['kind'], target: string, summary: string, payload: unknown): DataRequest {
  const item: DataRequest = {
    id: `${kind}-${Date.now()}`,
    kind,
    target,
    summary,
    requestedAt: stamp(),
    requestedBy: CURRENT_ADMIN.name,
    payload: JSON.parse(JSON.stringify(payload)),
  }
  state.items.unshift(item)
  return item
}

function remove(id: string) {
  const i = state.items.findIndex((x) => x.id === id)
  if (i >= 0) state.items.splice(i, 1)
}

export function useDataRequests() {
  return { state, add, remove }
}

/** 브라우저에서 JSON 파일로 내려받는다 */
export function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
