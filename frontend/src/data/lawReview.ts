// 법규 검토 화면의 예시 데이터.
// Spring에 협상안 검사(business-mcp check_offer) HTTP API가 생기면 이 파일 대신 API 응답을 쓴다.
// 판정 기준은 mcp-servers/business-mcp/README.md의 check_offer 표를 따른다.

export type Severity = 'violation' | 'warning' | 'info'

export interface ReviewRow {
  code: string
  severity: Severity
  condition: string
  result: string
  basis: string
  action: string
}

export const SAMPLE_OFFER = {
  supplier: 'SUP-0001 (예시)',
  rfq: 'RFQ-2026-001',
  unitPrice: 3800,
  paymentDays: 75,
  rawMaterialRatio: 40,
  priceLinkage: false,
}

export const SAMPLE_ROWS: ReviewRow[] = [
  {
    code: 'PAYMENT_DAYS_OVER_60',
    severity: 'violation',
    condition: '대금 지급기한 60일 초과',
    result: '75일 조건: 수령일부터 60일을 넘습니다. 예외(대등한 지위 등) 해당 여부는 법무 확인',
    basis: '하도급법 제13조①',
    action: '조건 수정',
  },
  {
    code: 'LINKAGE_TERMS_MISSING',
    severity: 'violation',
    condition: '납품대금 연동 약정',
    result: '원재료 비중 40%(주요 원재료 기준 10% 이상)인데 연동 약정이 없습니다',
    basis: '하도급법 제2조⑯, 제3조②',
    action: '근거 확인',
  },
  {
    code: 'PRICE_BELOW_MARKET',
    severity: 'info',
    condition: '견적 기준 단가',
    result: '과거 견적·시장 중앙값과 비교 (중앙값의 90% 미만이면 확인 필요)',
    basis: 'business-mcp',
    action: '정보 조회',
  },
  {
    code: 'CAPABILITY_CHECK',
    severity: 'info',
    condition: '생산능력 / 인증',
    result: '현재 RFQ의 필수 인증·생산능력 충족 여부',
    basis: 'business-mcp',
    action: '정보 조회',
  },
  {
    code: 'LAW_SOURCE',
    severity: 'info',
    condition: '적용 법령 원문',
    result: '기준 시행일에 맞는 조문을 별도로 조회',
    basis: 'law-mcp',
    action: '원문 확인',
  },
]

// law-mcp 색인(하도급법 시행 2026-10-02 판)에서 확인한 조문 요지
export const RELATED_ARTICLES = [
  {
    citation: '「하도급거래 공정화에 관한 법률」 제13조①(하도급대금의 지급 등)',
    summary: '목적물등의 수령일부터 60일 이내의 가능한 짧은 기한으로 정한 지급기일까지 하도급대금을 지급해야 합니다. 대등한 지위에서 정한 경우 등은 예외입니다.',
  },
  {
    citation: '「하도급거래 공정화에 관한 법률」 제2조⑯(정의: 주요 원재료)',
    summary: '목적물등의 제조에 사용되는 원재료로서 그 비용이 하도급대금의 100분의 10 이상인 원재료를 주요 원재료라고 합니다.',
  },
  {
    citation: '「하도급거래 공정화에 관한 법률」 제3조②(서면의 발급: 하도급대금 연동)',
    summary: '하도급계약 서면에 하도급대금 연동에 관한 사항을 적어야 합니다. 소기업, 단기·소액 거래 등은 제3조④의 예외가 있습니다.',
  },
]
