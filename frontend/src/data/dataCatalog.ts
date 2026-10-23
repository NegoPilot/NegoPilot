// 데이터 관리 화면의 기준값.
// 협력사: mcp-servers/business-mcp/schema.sql의 CHECK 제약과 scripts/seed.py 값을 옮겼다.
// 등록 API가 생기면 이 값도 API(catalog 등)에서 받는다.

/** capabilities.process CHECK 제약 */
export const PROCESSES = ['다이캐스팅', '프레스', '사출', '단조', 'CNC가공'] as const
export type Process = (typeof PROCESSES)[number]

/** 시드에 쓰인 공정별 소재. 목록에 없는 소재도 입력할 수 있다 */
export const MATERIALS: Record<Process, string[]> = {
  다이캐스팅: ['ADC12', 'A380'],
  프레스: ['SPCC', 'SPFH590', 'AL5052'],
  사출: ['PP', 'PA66', 'ABS'],
  단조: ['S45C', 'SCM440'],
  CNC가공: ['AL6061', 'S45C'],
}

/** 관리자 협력사 API가 받는 17개 시도 약칭 (AdminSupplierValidator) */
export const REGIONS = ['서울', '부산', '대구', '인천', '광주', '대전', '울산', '세종', '경기', '강원', '충북', '충남', '전북', '전남', '경북', '경남', '제주']

export const AUDIT_GRADES = ['A', 'B', 'C', 'D'] as const

/** 시드 협력사 SUP-0001 ~ SUP-0150 */
export const SEED_SUPPLIER_MAX = 150
