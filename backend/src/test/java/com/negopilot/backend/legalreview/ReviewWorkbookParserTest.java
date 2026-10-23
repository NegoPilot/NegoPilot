package com.negopilot.backend.legalreview;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.negopilot.backend.common.api.ApiException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;

class ReviewWorkbookParserTest {
    private static final UUID ID = UUID.randomUUID();

    static List<Object> headers() {
        return Arrays.asList("공급자 ID", "RFQ ID", "검토 기준일", "제안 단가(원)", "지급기한(일)", "계약 기간(일)", "연동 약정 포함",
                "원재료 비중(%)", "에너지 비중(%)", "미연동 합의",
                "공급자 KSIC", "공급자 평균매출액(억원)", "공급자 자산총액(억원)", "공급자 독립성", "공급자 관계기업", "공급자 유예·경과조치 검토", "공급자 규모 근거",
                "구매자 KSIC", "구매자 평균매출액(억원)", "구매자 자산총액(억원)", "구매자 독립성", "구매자 관계기업", "구매자 관계기업 합산 평균매출액",
                "구매자 유예·간주 중소기업", "구매자 유예·경과조치 검토", "구매자 규모 근거",
                "거래 유형", "당사자 요건 확인", "발주 구분", "최초 계약일", "갱신일", "실제 계약금액(원)", "확정 구매수량(개)",
                "기존 단가(원)", "감액 사유", "감액 증빙", "지급 기산점", "수령일", "약정 지급일", "기성금 수령 해당", "지급 예외 주장", "거래 증빙");
    }

    static List<Object> validRow() {
        return Arrays.asList("SUP-0034", "RFQ-2026-001", "2026-10-23", 3900, "60일", 365, "예",
                "40%", null, "아니오",
                "C30399", 329, "250", "예", "아니오", "완료", "QA-SUP",
                "C30121", 5000, 4000, "예", "아니오", 9999,
                "아니오", true, "QA-BUYER",
                "제조위탁", "예", "신규 발주", 46318, null, "456,000,000원", 120000,
                null, null, null, "수령일", "2026-11-02", "2026-12-31", "모름", "아니오", "계약서 A-1; 발주서 B-2");
    }

    @Test void readsOfferCompanyAndTransactionFacts() {
        var parsed = ReviewWorkbookParser.parse(TestXlsx.of(List.of(headers(), validRow())), ID);
        assertThat(parsed.rows()).hasSize(1);
        var row = parsed.rows().getFirst();
        assertThat(row.valid()).isTrue();
        assertThat(row.rowNumber()).isEqualTo(2);
        JsonNode offer = row.offer();
        assertThat(offer.get("supplier").stringValue()).isEqualTo("SUP-0034");
        assertThat(offer.get("as_of").stringValue()).isEqualTo("2026-10-23");
        assertThat(offer.get("unit_price").intValue()).isEqualTo(3900);
        assertThat(offer.get("payment_days").intValue()).isEqualTo(60);
        assertThat(offer.get("raw_material_ratio").doubleValue()).isEqualTo(0.4);
        assertThat(offer.get("energy_ratio").isNull()).isTrue();
        assertThat(offer.get("linkage_waived_by_agreement").booleanValue()).isFalse();

        JsonNode supplier = offer.get("supplier_size");
        assertThat(supplier.get("assets_eok").doubleValue()).isEqualTo(250);
        assertThat(supplier.get("transition_reviewed").booleanValue()).isTrue();
        assertThat(supplier.get("seat_manufacturing").isNull()).isTrue();
        JsonNode buyer = offer.get("buyer_size");
        // 관계기업이 아니오면 합산 매출은 보내지 않는다
        assertThat(buyer.get("related_aggregate_revenue_eok").isNull()).isTrue();
        assertThat(buyer.get("deemed_sme").booleanValue()).isFalse();
        assertThat(buyer.size()).isEqualTo(supplier.size()).isEqualTo(11);

        JsonNode tx = offer.get("transaction");
        assertThat(tx.get("transaction_type").stringValue()).isEqualTo("manufacturing");
        assertThat(tx.get("party_requirements_confirmed").booleanValue()).isTrue();
        assertThat(tx.get("price_scope").stringValue()).isEqualTo("new_order");
        assertThat(tx.get("contracted_on").stringValue()).isEqualTo("2026-10-23");
        assertThat(tx.get("renewed_on").isNull()).isTrue();
        assertThat(tx.get("actual_contract_amount").longValue()).isEqualTo(456_000_000L);
        assertThat(tx.get("committed_quantity").intValue()).isEqualTo(120000);
        assertThat(tx.get("payment_basis").stringValue()).isEqualTo("receipt");
        assertThat(tx.get("upstream_payment_applicable").isNull()).isTrue();
        assertThat(tx.get("payment_exception_claimed").booleanValue()).isFalse();
        assertThat(tx.get("reduction_evidence").isEmpty()).isTrue();
        assertThat(tx.get("evidence_references").toString()).isEqualTo("[\"계약서 A-1\",\"발주서 B-2\"]");
        assertThat(tx.get("linkage_details").isEmpty()).isTrue();
        // 엑셀에 없는 거래 사실 열은 미확인으로 보낸다
        assertThat(tx.get("completion_date").isNull()).isTrue();
        assertThat(tx.get("waiver_evidence").isEmpty()).isTrue();

        assertThat(row.source().get("지급기한(일)").stringValue()).isEqualTo("60일");
        assertThat(row.source().get("최초 계약일").doubleValue()).isEqualTo(46318);
    }

    @Test void badRowsAreRejectedOneByOneAndGoodRowsStay() {
        List<Object> missing = new ArrayList<>(validRow());
        missing.set(0, null);          // 공급자 ID 비움
        missing.set(2, "2026-02-30");  // 없는 날짜
        List<Object> units = new ArrayList<>(validRow());
        units.set(3, 3900.5);          // 정수 아님
        units.set(7, "150%");          // 비중 범위 밖
        units.set(26, "외주");          // 거래 유형 값 오류
        List<Object> unknownFacts = new ArrayList<>(validRow());
        for (int i = 10; i < unknownFacts.size(); i++) unknownFacts.set(i, null);

        var rows = ReviewWorkbookParser.parse(TestXlsx.of(List.of(headers(), missing, units, unknownFacts)), ID).rows();
        assertThat(rows).extracting(ReviewWorkbookParser.Row::rowNumber).containsExactly(2, 3, 4);
        assertThat(rows.get(0).valid()).isFalse();
        assertThat(rows.get(0).offer()).isNull();
        assertThat(rows.get(0).errors()).extracting(e -> e.get("column")).containsExactlyInAnyOrder("공급자 ID", "검토 기준일");
        assertThat(rows.get(1).errors()).extracting(e -> e.get("column")).containsExactlyInAnyOrder("제안 단가", "원재료 비중", "거래 유형");
        // 판단에 필요한 사실이 비어 있어도 요청은 만든다
        var open = rows.get(2);
        assertThat(open.valid()).isTrue();
        assertThat(open.offer().get("supplier_size").get("independence_met").isNull()).isTrue();
        assertThat(open.offer().get("transaction").get("transaction_type").stringValue()).isEqualTo("unknown");
        assertThat(open.offer().get("transaction").get("payment_basis").stringValue()).isEqualTo("unknown");
        assertThat(open.offer().get("transaction").get("party_requirements_confirmed").isNull()).isTrue();
    }

    @Test void linkageSheetAttachesBySupplierAndRfq() {
        List<Object> other = new ArrayList<>(validRow());
        other.set(0, "SUP-0060");
        other.set(1, "RFQ-2026-002");
        Map<String, List<List<Object>>> sheets = new LinkedHashMap<>();
        sheets.put("거래조건", List.of(headers(), validRow(), other));
        sheets.put("연동상세", List.of(
                Arrays.asList("공급자 ID", "RFQ ID", "구분", "대상 이름", "원가 비중(%)", "기준 지표", "조정 산식", "조정요건(%)", "조정 주기", "증빙"),
                Arrays.asList("SUP-0034", "RFQ-2026-001", "원재료", "ADC12", "40%", "LME 알루미늄", "기준가×변동률", "3%", "분기", "연동약정서 1"),
                Arrays.asList("SUP-0034", "RFQ-2026-001", "에너지", "전력", 0.08, null, null, 0.05, null, null),
                Arrays.asList("SUP-0099", "RFQ-2026-001", "원재료", "강판", null, null, null, null, null, null),
                Arrays.asList("SUP-0060", "RFQ-2026-002", "원재료", "", null, null, null, "20%", null, null)));

        var parsed = ReviewWorkbookParser.parse(TestXlsx.of(sheets), ID);
        var first = parsed.rows().get(0);
        JsonNode details = first.offer().get("transaction").get("linkage_details");
        assertThat(details).hasSize(2);
        assertThat(details.get(0).get("category").stringValue()).isEqualTo("material");
        assertThat(details.get(0).get("cost_ratio").doubleValue()).isEqualTo(0.4);
        assertThat(details.get(0).get("trigger_ratio").doubleValue()).isEqualTo(0.03);
        assertThat(details.get(0).has("supplier")).isFalse();
        assertThat(details.get(0).get("evidence_references").get(0).stringValue()).isEqualTo("연동약정서 1");
        assertThat(details.get(1).get("category").stringValue()).isEqualTo("energy");
        assertThat(first.source().get("연동상세")).hasSize(2);

        // 연동 상세 행의 오류는 연결된 거래 행의 오류가 된다
        var second = parsed.rows().get(1);
        assertThat(second.valid()).isFalse();
        assertThat(second.errors()).extracting(e -> e.get("column"))
                .containsExactlyInAnyOrder("연동상세 5행 · 대상 이름", "연동상세 5행 · 조정요건");
        // 연결할 거래가 없는 행은 파일 단위 오류로 남는다
        assertThat(parsed.sheetErrors()).singleElement().satisfies(e -> assertThat(e.get("row")).isEqualTo(4));
    }

    @Test void ambiguousLinkageBlocksBothRows() {
        Map<String, List<List<Object>>> sheets = new LinkedHashMap<>();
        sheets.put("거래조건", List.of(headers(), validRow(), validRow()));
        sheets.put("연동상세", List.of(Arrays.asList("공급자 ID", "RFQ ID", "구분", "대상 이름"),
                Arrays.asList("SUP-0034", "RFQ-2026-001", "원재료", "ADC12")));
        var parsed = ReviewWorkbookParser.parse(TestXlsx.of(sheets), ID);
        assertThat(parsed.rows()).allSatisfy(r -> assertThat(r.valid()).isFalse());
        assertThat(parsed.sheetErrors()).hasSize(1);
    }

    @Test void headerProblemsRejectTheWholeFile() {
        assertRejected(TestXlsx.of(List.of(Arrays.asList("공급자 ID", "RFQ ID", "메모"), Arrays.asList("SUP-0001", "RFQ-2026-001", "x"))),
                "거래조건 · 메모", "거래조건 · 검토 기준일");
        List<Object> duplicate = new ArrayList<>(headers());
        duplicate.add("단가");
        assertRejected(TestXlsx.of(List.of(duplicate, validRow())), "거래조건 · 단가");
        assertRejected(TestXlsx.of(List.of(headers())), "거래조건");
        List<List<Object>> many = new ArrayList<>();
        many.add(headers());
        for (int i = 0; i <= ReviewWorkbookParser.MAX_DATA_ROWS; i++) many.add(validRow());
        assertRejected(TestXlsx.of(many), "거래조건");
    }

    @Test void rejectsNonWorkbooksAndDoctype() {
        assertRejected("not a zip".getBytes(), "파일");
        assertRejected(TestXlsx.zip(Map.of("xl/workbook.xml", "<!DOCTYPE x [<!ENTITY e SYSTEM \"file:///etc/passwd\">]><x>&e;</x>",
                "xl/_rels/workbook.xml.rels", "<x/>")), "파일");
    }

    private static void assertRejected(byte[] data, String... columns) {
        assertThatThrownBy(() -> ReviewWorkbookParser.parse(data, ID)).isInstanceOfSatisfying(ApiException.class, e -> {
            assertThat(e.status.value()).isEqualTo(400);
            @SuppressWarnings("unchecked")
            var errors = (List<Map<String, String>>) ((Map<String, Object>) e.details).get("errors");
            assertThat(errors).extracting(m -> m.get("column")).contains(columns);
        });
    }
}
