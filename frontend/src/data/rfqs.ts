// business-mcp 시드 데이터(mcp-servers/business-mcp/scripts/seed.py의 RFQS)를 화면에 옮긴 것.
// Spring에 RFQ 조회 API(list_rfqs)가 생기면 이 파일 대신 API를 부른다.
export interface RfqRow {
  rfq_id: string
  part_name: string
  process: string
  material: string
  annual_volume: number
  target_price: number
}

export const SEED_RFQS: RfqRow[] = [
  { rfq_id: 'RFQ-2026-001', part_name: '알루미늄 브래킷', process: '다이캐스팅', material: 'ADC12', annual_volume: 120000, target_price: 3900 },
  { rfq_id: 'RFQ-2026-002', part_name: '시트 레일 보강판', process: '프레스', material: 'SPFH590', annual_volume: 240000, target_price: 1750 },
  { rfq_id: 'RFQ-2026-003', part_name: '배터리 커버 클립', process: '사출', material: 'PA66', annual_volume: 600000, target_price: 1450 },
  { rfq_id: 'RFQ-2026-004', part_name: '구동축 플랜지', process: '단조', material: 'SCM440', annual_volume: 80000, target_price: 4100 },
  { rfq_id: 'RFQ-2026-005', part_name: '센서 하우징', process: 'CNC가공', material: 'AL6061', annual_volume: 50000, target_price: 5900 },
  { rfq_id: 'RFQ-2026-006', part_name: '모터 브래킷', process: '다이캐스팅', material: 'A380', annual_volume: 90000, target_price: 4400 },
]

/** business-mcp 시드의 협력사 수, law-mcp 색인의 법령 수 (README 기준) */
export const SEED_SUPPLIER_COUNT = 150
export const LAW_COUNT = 6
