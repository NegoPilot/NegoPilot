// 로그인·회원가입·관리자 화면의 예시 데이터 (Figma 07~11).
// 인증·회원 API가 아직 없어서 화면 안에서만 쓴다. API가 생기면 이 파일 대신 응답을 쓴다.

export type MemberRole = 'admin' | 'lead' | 'staff'
export type MemberStatus = 'active' | 'suspended'

export const ROLE_LABEL: Record<MemberRole, string> = {
  admin: '관리자',
  lead: '팀장',
  staff: '실무자',
}

export const STATUS_LABEL: Record<MemberStatus, string> = {
  active: '활성',
  suspended: '이용 정지',
}

/** 가입 신청의 기본 역할. 팀장·관리자 권한은 승인 뒤 관리자가 따로 준다 */
export const DEFAULT_SIGNUP_ROLE_LABEL = '구매 실무자'

export interface Member {
  name: string
  email: string
  department: string
  role: MemberRole
  status: MemberStatus
  lastAccess: string
}

export interface SignupRequest {
  name: string
  email: string
  department: string
  position: string
  company: string
  requestedAt: string
}

export interface AdminLog {
  at: string
  actor: string
  action: string
  target: string
}

export const ORGANIZATION = {
  name: '한빛오토텍',
  /** 시연용 회사 코드. 다른 코드도 받지만 소속 회사는 '확인 중'으로 표시한다 */
  code: 'HANBIT-2026',
  domain: 'hanbit-auto.co.kr',
}

export const CURRENT_ADMIN = { name: '김관리', department: '관리팀', email: 'admin@hanbit-auto.co.kr' }

const d = ORGANIZATION.domain

export const SEED_MEMBERS: Member[] = [
  { name: '김관리', email: `admin@${d}`, department: '관리팀', role: 'admin', status: 'active', lastAccess: '10.21 10:40' },
  { name: '이도현', email: `dohyeon@${d}`, department: '구매2팀', role: 'lead', status: 'active', lastAccess: '10.21 10:15' },
  { name: '최수빈', email: `subin@${d}`, department: '구매1팀', role: 'staff', status: 'active', lastAccess: '10.21 09:45' },
  { name: '정민수', email: `minsu@${d}`, department: '구매1팀', role: 'staff', status: 'suspended', lastAccess: '10.19 17:20' },
  { name: '박서연', email: `seoyeon@${d}`, department: '구매1팀', role: 'lead', status: 'active', lastAccess: '10.21 09:30' },
  { name: '한지우', email: `jiwoo@${d}`, department: '구매1팀', role: 'staff', status: 'active', lastAccess: '10.21 09:12' },
  { name: '오태윤', email: `taeyun@${d}`, department: '구매2팀', role: 'staff', status: 'active', lastAccess: '10.21 08:58' },
  { name: '윤하늘', email: `haneul@${d}`, department: '구매2팀', role: 'staff', status: 'active', lastAccess: '10.20 18:05' },
  { name: '서민재', email: `minjae@${d}`, department: '구매3팀', role: 'lead', status: 'active', lastAccess: '10.20 17:44' },
  { name: '강유나', email: `yuna@${d}`, department: '구매3팀', role: 'staff', status: 'active', lastAccess: '10.20 17:10' },
  { name: '조현우', email: `hyunwoo@${d}`, department: '구매3팀', role: 'staff', status: 'active', lastAccess: '10.20 16:32' },
  { name: '임채원', email: `chaewon@${d}`, department: '구매1팀', role: 'staff', status: 'active', lastAccess: '10.20 15:20' },
  { name: '신동혁', email: `donghyuk@${d}`, department: '원가기획팀', role: 'lead', status: 'active', lastAccess: '10.20 14:48' },
  { name: '권소율', email: `soyul@${d}`, department: '원가기획팀', role: 'staff', status: 'active', lastAccess: '10.20 13:05' },
  { name: '황인호', email: `inho@${d}`, department: '구매2팀', role: 'staff', status: 'active', lastAccess: '10.20 11:41' },
  { name: '문예린', email: `yerin@${d}`, department: '구매1팀', role: 'staff', status: 'active', lastAccess: '10.20 10:27' },
  { name: '배준서', email: `junseo@${d}`, department: '구매3팀', role: 'staff', status: 'active', lastAccess: '10.19 18:12' },
  { name: '송다은', email: `daeun@${d}`, department: '원가기획팀', role: 'staff', status: 'active', lastAccess: '10.19 16:55' },
  { name: '유건우', email: `gunwoo@${d}`, department: '구매2팀', role: 'staff', status: 'active', lastAccess: '10.19 15:03' },
  { name: '노시은', email: `sieun@${d}`, department: '구매1팀', role: 'staff', status: 'active', lastAccess: '10.19 13:40' },
  { name: '전우진', email: `woojin@${d}`, department: '구매3팀', role: 'staff', status: 'active', lastAccess: '10.18 17:22' },
  { name: '남궁별', email: `byeol@${d}`, department: '구매2팀', role: 'staff', status: 'active', lastAccess: '10.18 11:09' },
  { name: '차은호', email: `eunho@${d}`, department: '원가기획팀', role: 'staff', status: 'active', lastAccess: '10.17 16:48' },
  { name: '장미래', email: `mirae@${d}`, department: '구매1팀', role: 'staff', status: 'active', lastAccess: '10.16 10:30' },
]

export const SEED_REQUESTS: SignupRequest[] = [
  { name: '김가연', email: `gayeon@${d}`, department: '구매1팀', position: '매니저', company: ORGANIZATION.name, requestedAt: '2026.10.21' },
  { name: '이서준', email: `seojun@${d}`, department: '구매2팀', position: '', company: ORGANIZATION.name, requestedAt: '2026.10.21' },
  { name: '박지민', email: `jimin@${d}`, department: '구매1팀', position: '', company: ORGANIZATION.name, requestedAt: '2026.10.20' },
]

export const SEED_LOGS: AdminLog[] = [
  { at: '10.21 10:32', actor: '김관리', action: '회원 역할 변경 · 실무자 → 팀장', target: '이도현 / 구매2팀' },
  { at: '10.21 09:18', actor: '김관리', action: '가입 승인 · 구매 실무자', target: '최수빈 / 구매1팀' },
  { at: '10.20 17:42', actor: '김관리', action: '계정 이용 정지', target: '정민수 / 퇴사' },
]
