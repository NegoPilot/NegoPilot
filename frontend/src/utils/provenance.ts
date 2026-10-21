// 실행 모드와 데이터의 출처를 구분한다.
export function provenanceLabel(source: string | null | undefined): string {
  switch (source?.toUpperCase()) {
    case 'DART': return 'DART 공시'
    case 'MOCK': return 'MOCK 가상 데이터'
    case 'STUB': return 'STUB 테스트 응답'
    case 'MANUAL': return '관리자 입력'
    case 'MIXED': return '출처 혼합'
    case 'UNKNOWN': return '출처 정보 없음'
    default: return source || '출처 정보 없음'
  }
}
