// 데이터 출처 표시. STUB(연결 검증용 가상 데이터)과 MOCK(가상 시드)은 화면에서 모두 'MOCK'으로 보여 준다
export function provenanceLabel(source: string | null | undefined): string {
  if (!source) return '출처 없음'
  const upper = source.toUpperCase()
  if (upper === 'STUB' || upper === 'MOCK') return 'MOCK'
  if (upper === 'DART') return 'DART 공시'
  return source
}
