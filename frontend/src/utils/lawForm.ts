// 법령 등록 폼. law-mcp는 DB가 아니라 파일을 읽는다 (mcp-servers/law-mcp/README.md).
// 1) legalize-kr 경로를 scripts/fetch_laws.py의 TARGETS에 넣고 내려받거나
// 2) 같은 형식(frontmatter + '##### 제N조')의 원문 Markdown을 data/raw/{law_key}/에 넣은 뒤
// scripts/build_index.py로 색인을 다시 만든다.

export const LAW_KINDS = ['법률', '시행령', '시행규칙'] as const

export const LAW_KEY_RE = /^[a-z][a-z0-9_]{1,40}$/

/** legalize-kr 경로는 kr/{법령명 공백 제거}/{구분}.md */
export function suggestLegalizePath(name: string, kind: string): string {
  const compact = name.replace(/\s+/g, '')
  return compact ? `kr/${compact}/${kind}.md` : ''
}

export function isLegalizePath(path: string): boolean {
  return /^kr\/[^/]+\/[^/]+\.md$/.test(path.trim())
}

/** 별칭 입력('하도급법, 하도급법 시행령')을 server.py ALIASES 키 형식(공백·가운뎃점 제거)으로 */
export function aliasKeys(raw: string): string[] {
  return [...new Set(raw.split(',').map((a) => a.replace(/[\sㆍ·]/g, '')).filter(Boolean))]
}

export interface RawLawSummary {
  fields: Record<string, string>
  title: string | null
  promulgated: string | null
  effective: string | null
  source: string | null
  articleCount: number
  problems: string[]
}

/** 원문 앞의 '---' 사이 frontmatter에서 한 줄짜리 'key: value'만 읽는다 */
export function readRawLaw(text: string): RawLawSummary {
  const fields: Record<string, string> = {}
  const problems: string[] = []
  const m = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text)
  if (!m) problems.push("맨 앞에 '---'로 감싼 frontmatter가 없습니다.")
  else {
    for (const line of (m[1] ?? '').split(/\r?\n/)) {
      const kv = /^([^\s:#-][^:]*):\s*(.*)$/.exec(line)
      if (kv?.[1]) fields[kv[1].trim()] = (kv[2] ?? '').trim().replace(/^'(.*)'$/, '$1')
    }
  }
  const date = (key: string) => {
    const v = fields[key]
    return v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : null
  }
  const effective = date('시행일자')
  if (m && !effective) problems.push('시행일자(YYYY-MM-DD)가 없습니다. 판을 고를 때 이 값을 씁니다.')
  const articleCount = (text.match(/^#####\s*제\d+조/gm) ?? []).length
  if (!articleCount) problems.push("'##### 제1조 (목적)' 형식의 조문 제목을 찾지 못했습니다.")
  return {
    fields,
    title: fields['제목'] || null,
    promulgated: date('공포일자'),
    effective,
    source: fields['출처'] || null,
    articleCount,
    problems,
  }
}
