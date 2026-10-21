// 계약 규칙: null은 0으로 바꾸지 않고 "정보 없음"으로 보여 준다. 백분율 값에 100을 곱하지 않는다.
export const NO_DATA = '정보 없음'

const integer = new Intl.NumberFormat('ko-KR')
const oneDecimal = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1 })

export function formatInteger(value: number | null | undefined): string {
  return value == null ? NO_DATA : integer.format(value)
}

export function formatKrw(value: number | null | undefined): string {
  return value == null ? NO_DATA : `${integer.format(value)}원`
}

/** 매출처럼 큰 금액: 1억 원 이상은 억 단위로 줄인다 */
export function formatKrwCompact(value: number | null | undefined): string {
  if (value == null) return NO_DATA
  if (Math.abs(value) >= 100_000_000) return `${oneDecimal.format(value / 100_000_000)}억 원`
  return formatKrw(value)
}

export function formatPercent(value: number | null | undefined): string {
  return value == null ? NO_DATA : `${oneDecimal.format(value)}%`
}

export function formatPpm(value: number | null | undefined): string {
  return value == null ? NO_DATA : `${integer.format(value)} ppm`
}

export function formatCapacity(value: number | null | undefined): string {
  return value == null ? NO_DATA : `${integer.format(value)} EA/월`
}

/** "50,000" 같은 입력을 양의 정수로. 숫자가 아니면 null */
export function parsePositiveInteger(raw: string): number | null {
  const cleaned = raw.replace(/[,\s]/g, '')
  if (!/^\d+$/.test(cleaned)) return null
  const value = Number(cleaned)
  return Number.isSafeInteger(value) && value > 0 ? value : null
}
