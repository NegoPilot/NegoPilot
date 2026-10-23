// 데이터 관리 화면의 기준값.
// 협력사: mcp-servers/business-mcp/schema.sql의 CHECK 제약과 scripts/seed.py 값을 옮겼다.
// 법령: mcp-servers/law-mcp/data/raw/manifest.json과 README의 법령 표를 옮겼다.
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

export interface StoredLaw {
  key: string
  name: string
  alias: string
  path: string
  /** 저장된 판의 시행일자 */
  versions: string[]
}

export const LEGALIZE_REPO = 'https://github.com/legalize-kr/legalize-kr'

export const STORED_LAWS: StoredLaw[] = [
  {
    key: 'subcontract_act',
    name: '하도급거래 공정화에 관한 법률',
    alias: '하도급법',
    path: 'kr/하도급거래공정화에관한법률/법률.md',
    versions: ['2025-01-21', '2025-04-01', '2025-12-17', '2026-08-11', '2026-10-02', '2027-09-09'],
  },
  {
    key: 'subcontract_decree',
    name: '하도급거래 공정화에 관한 법률 시행령',
    alias: '하도급법 시행령',
    path: 'kr/하도급거래공정화에관한법률/시행령.md',
    versions: ['2024-05-28', '2024-06-08', '2024-12-27', '2025-10-01', '2026-07-01', '2026-08-11'],
  },
  {
    key: 'winwin_act',
    name: '대ㆍ중소기업 상생협력 촉진에 관한 법률',
    alias: '상생협력법',
    path: 'kr/대ㆍ중소기업상생협력촉진에관한법률/법률.md',
    versions: ['2026-01-02', '2026-06-03', '2026-07-01', '2026-09-29', '2028-02-20'],
  },
  {
    key: 'winwin_decree',
    name: '대ㆍ중소기업 상생협력 촉진에 관한 법률 시행령',
    alias: '상생협력법 시행령',
    path: 'kr/대ㆍ중소기업상생협력촉진에관한법률/시행령.md',
    versions: ['2025-02-21', '2025-07-01', '2025-10-01', '2025-12-23', '2026-06-03', '2026-07-01'],
  },
  {
    key: 'sme_act',
    name: '중소기업기본법',
    alias: '중소기업기본법',
    path: 'kr/중소기업기본법/법률.md',
    versions: ['2024-08-21', '2024-08-28', '2026-02-19', '2026-07-01', '2027-01-01', '2027-03-30'],
  },
  {
    key: 'sme_decree',
    name: '중소기업기본법 시행령',
    alias: '중소기업기본법 시행령',
    path: 'kr/중소기업기본법/시행령.md',
    versions: ['2024-07-10', '2024-08-21', '2025-09-01', '2025-10-01', '2025-12-23', '2026-01-02'],
  },
]

/** 기준일에 시행 중인 판과 아직 시행 전인 판 */
export function lawVersionsAsOf(law: { versions: string[] }, asOf: string) {
  const sorted = [...law.versions].sort()
  const effective = sorted.filter((v) => v <= asOf)
  return { current: effective[effective.length - 1] ?? null, upcoming: sorted.filter((v) => v > asOf) }
}
