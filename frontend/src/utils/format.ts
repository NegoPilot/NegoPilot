// 계약 규칙: null은 0으로 바꾸지 않고 "정보 없음"으로 보여 준다. 백분율 값에 100을 곱하지 않는다.
export const NO_DATA = '정보 없음'

/** 인증 보유 여부. 값이 없으면 '정보 없음' */
export function formatHeld(value: boolean | null | undefined): string {
  return value == null ? NO_DATA : value ? '보유' : '미보유'
}

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

const dateTime = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
})

/** 한국 날짜 기준 오늘 (YYYY-MM-DD) */
export function todayIso(): string {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul' }).format(new Date())
}

/** 서버 시각(ISO)을 한국 시간으로. 읽을 수 없으면 '확인 불가' */
export function formatDateTime(value: string): string {
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? dateTime.format(timestamp) : '확인 불가'
}

/** "50,000" 같은 입력을 양의 정수로. 숫자가 아니면 null */
export function parsePositiveInteger(raw: string): number | null {
  const cleaned = raw.replace(/[,\s]/g, '')
  if (!/^\d+$/.test(cleaned)) return null
  const value = Number(cleaned)
  return Number.isSafeInteger(value) && value > 0 ? value : null
}
