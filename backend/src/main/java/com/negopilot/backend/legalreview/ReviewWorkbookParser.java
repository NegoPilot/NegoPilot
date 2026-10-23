package com.negopilot.backend.legalreview;

import com.negopilot.backend.common.api.ApiException;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

/**
 * 엑셀 → 행별 법규 검토 요청 offer. 한 행이 검토 한 건이고, 연동 상세는 "연동상세" 시트에서 공급자 ID + RFQ ID로 연결한다.
 * 필수 필드 오류는 그 행만 INVALID로 만들고, 판단에 필요한 사실이 비어 있으면 null/unknown으로 보낸다.
 * 열 목록과 값 규칙은 docs/contracts/legal-review-upload.md 2절.
 */
final class ReviewWorkbookParser {
    static final int MAX_DATA_ROWS = 30;
    static final int MAX_LINKAGE_ROWS = 300;
    static final String MAIN_SHEET = "거래조건";
    static final String LINKAGE_SHEET = "연동상세";

    enum Kind { TEXT, SUPPLIER_ID, RFQ_ID, POSITIVE_INT, RATIO, TRIGGER_RATIO, EOK, BOOL, BOOL_FALSE, TRI, DATE, LIST, ENUM }

    /** path는 offer 안의 위치(예: "transaction.receipt_date"). enumValues는 ENUM 칸의 한글 값 → API 값. */
    record Column(String label, String path, Kind kind, boolean required, Map<String, String> enumValues, String... aliases) { }

    record Row(int rowNumber, ObjectNode source, ObjectNode offer, List<Map<String, String>> errors,
               String supplierId, String rfqId) {
        boolean valid() { return errors.isEmpty(); }
    }

    record Parsed(List<Row> rows, List<Map<String, Object>> sheetErrors) { }

    private static final Map<String, String> TRANSACTION_TYPE = Map.of("제조위탁", "manufacturing", "제조", "manufacturing",
            "구매", "purchase", "모름", "unknown");
    private static final Map<String, String> PRICE_SCOPE = Map.of("신규", "new_order", "신규발주", "new_order",
            "기존", "existing_order", "기존발주", "existing_order", "모름", "unknown");
    private static final Map<String, String> PAYMENT_BASIS = Map.of("수령일", "receipt", "목적물수령일", "receipt",
            "완료일", "completion", "용역완료일", "completion", "월합의일", "monthly_agreed", "계산서발행일", "monthly_agreed",
            "모름", "unknown");
    private static final Map<String, String> CATEGORY = Map.of("원재료", "material", "에너지", "energy");
    private static final Set<String> TRUE = Set.of("예", "y", "yes", "o", "true", "1", "포함", "있음", "해당", "완료");
    private static final Set<String> FALSE = Set.of("아니오", "아니요", "n", "no", "x", "false", "0", "미포함", "없음",
            "해당없음", "미완료");
    private static final Set<String> UNKNOWN = Set.of("모름", "미확인", "-");
    private static final LocalDate EXCEL_EPOCH = LocalDate.of(1899, 12, 30);

    static final List<Column> MAIN = new ArrayList<>();
    static final List<Column> LINKAGE = List.of(
            new Column("공급자 ID", "supplier", Kind.SUPPLIER_ID, true, null, "공급자", "협력사", "협력사id"),
            new Column("RFQ ID", "rfq_id", Kind.RFQ_ID, true, null, "rfq"),
            new Column("구분", "category", Kind.ENUM, true, CATEGORY, "대상구분"),
            new Column("대상 이름", "name", Kind.TEXT, true, null, "대상", "원가대상"),
            new Column("원가 비중", "cost_ratio", Kind.RATIO, false, null, "비중"),
            new Column("기준 지표", "indicator", Kind.TEXT, false, null, "지표"),
            new Column("조정 산식", "formula", Kind.TEXT, false, null, "산식"),
            new Column("조정요건", "trigger_ratio", Kind.TRIGGER_RATIO, false, null, "조정요건변동률"),
            new Column("조정 주기", "frequency", Kind.TEXT, false, null, "주기"),
            new Column("기준·비교 시점", "reference_period", Kind.TEXT, false, null, "비교시점"),
            new Column("조정 반영일", "reflection_date", Kind.TEXT, false, null, "반영일", "반영시점"),
            new Column("증빙", "evidence_references", Kind.LIST, false, null, "증빙자료"));

    static {
        main("공급자 ID", "supplier", Kind.SUPPLIER_ID, true, null, "공급자", "협력사", "협력사id");
        main("RFQ ID", "rfq_id", Kind.RFQ_ID, true, null, "rfq");
        main("검토 기준일", "as_of", Kind.DATE, true, null, "기준일");
        main("제안 단가", "unit_price", Kind.POSITIVE_INT, true, null, "단가");
        main("지급기한", "payment_days", Kind.POSITIVE_INT, true, null, "지급일수");
        main("계약 기간", "contract_days", Kind.POSITIVE_INT, true, null, "거래기간");
        main("연동 약정 포함", "price_linkage", Kind.BOOL, true, null, "연동", "연동약정");
        main("원재료 비중", "raw_material_ratio", Kind.RATIO, false, null);
        main("에너지 비중", "energy_ratio", Kind.RATIO, false, null, "에너지비용비중");
        main("미연동 합의", "linkage_waived_by_agreement", Kind.BOOL_FALSE, false, null);
        for (String[] company : new String[][]{{"공급자", "supplier_size", "협력사"}, {"구매자", "buyer_size", null}}) {
            String p = company[0], key = company[1], alt = company[2];
            company(p, alt, "KSIC", key + ".ksic_code", Kind.TEXT, "업종코드", "ksic업종코드");
            company(p, alt, "평균매출액", key + ".average_revenue_eok", Kind.EOK);
            company(p, alt, "자산총액", key + ".assets_eok", Kind.EOK);
            company(p, alt, "독립성", key + ".independence_met", Kind.TRI, "독립성요건충족");
            company(p, alt, "관계기업", key + ".has_related_enterprises", Kind.TRI, "관계기업있음");
            company(p, alt, "관계기업 합산 평균매출액", key + ".related_aggregate_revenue_eok", Kind.EOK, "관계기업합산매출액");
            company(p, alt, "관계기업 합산 계산 검증", key + ".related_calculation_verified", Kind.BOOL_FALSE, "관계기업계산검증");
            company(p, alt, "의자·좌석 제조업", key + ".seat_manufacturing", Kind.TRI, "의자제조업");
            company(p, alt, "유예·경과조치 검토", key + ".transition_reviewed", Kind.BOOL_FALSE, "경과조치검토");
            company(p, alt, "유예·간주 중소기업", key + ".deemed_sme", Kind.TRI, "간주중소기업");
            company(p, alt, "규모 근거", key + ".evidence_reference", Kind.TEXT, "근거", "확인자료");
        }
        main("거래 유형", "transaction.transaction_type", Kind.ENUM, false, TRANSACTION_TYPE);
        main("당사자 요건 확인", "transaction.party_requirements_confirmed", Kind.TRI, false, null);
        main("발주 구분", "transaction.price_scope", Kind.ENUM, false, PRICE_SCOPE, "신규기존발주", "단가적용범위");
        main("최초 계약일", "transaction.contracted_on", Kind.DATE, false, null);
        main("갱신일", "transaction.renewed_on", Kind.DATE, false, null);
        main("실제 계약금액", "transaction.actual_contract_amount", Kind.POSITIVE_INT, false, null);
        main("확정 구매수량", "transaction.committed_quantity", Kind.POSITIVE_INT, false, null);
        main("기존 단가", "transaction.previous_unit_price", Kind.POSITIVE_INT, false, null, "기존발주단가");
        main("감액 사유", "transaction.reduction_reason", Kind.TEXT, false, null);
        main("감액 증빙", "transaction.reduction_evidence", Kind.LIST, false, null);
        main("지급 기산점", "transaction.payment_basis", Kind.ENUM, false, PAYMENT_BASIS);
        main("수령일", "transaction.receipt_date", Kind.DATE, false, null, "목적물수령일");
        main("완료일", "transaction.completion_date", Kind.DATE, false, null, "용역완료일");
        main("계산서 발행 합의일", "transaction.monthly_agreed_date", Kind.DATE, false, null, "월합의일");
        main("약정 지급일", "transaction.agreed_due_date", Kind.DATE, false, null);
        main("기성금 수령 해당", "transaction.upstream_payment_applicable", Kind.TRI, false, null, "기성금수령여부");
        main("기성금 수령일", "transaction.upstream_received_on", Kind.DATE, false, null);
        main("지급 예외 주장", "transaction.payment_exception_claimed", Kind.TRI, false, null);
        main("지급 예외 사유", "transaction.payment_exception_reason", Kind.TEXT, false, null);
        main("지급 예외 증빙", "transaction.payment_exception_evidence", Kind.LIST, false, null);
        main("미연동 합의 사유", "transaction.waiver_reason", Kind.TEXT, false, null);
        main("미연동 합의 증빙", "transaction.waiver_evidence", Kind.LIST, false, null);
        main("거래 증빙", "transaction.evidence_references", Kind.LIST, false, null);
    }

    private static void main(String label, String path, Kind kind, boolean required, Map<String, String> values,
                             String... aliases) {
        MAIN.add(new Column(label, path, kind, required, values, aliases));
    }

    private static void company(String prefix, String altPrefix, String item, String path, Kind kind, String... aliases) {
        List<String> names = new ArrayList<>();
        for (String p : altPrefix == null ? new String[]{prefix} : new String[]{prefix, altPrefix}) {
            names.add(p + item);
            for (String alias : aliases) names.add(p + alias);
        }
        MAIN.add(new Column(prefix + " " + item, path, kind, false, null, names.toArray(String[]::new)));
    }

    private ReviewWorkbookParser() { }

    static Parsed parse(byte[] data, UUID requestId) {
        Map<String, List<List<Object>>> sheets;
        try { sheets = XlsxReader.sheets(data); }
        catch (XlsxReader.InvalidWorkbookException e) { throw invalid(requestId, List.of(error("파일", e.getMessage()))); }
        Map<String, List<List<Object>>> byName = new HashMap<>();
        sheets.forEach((name, rows) -> byName.put(normalize(name), rows));
        List<List<Object>> main = byName.getOrDefault(normalize(MAIN_SHEET), sheets.values().iterator().next());
        List<List<Object>> linkage = byName.get(normalize(LINKAGE_SHEET));

        Table table = Table.of(main, MAIN, MAIN_SHEET, requestId);
        if (table.data.isEmpty()) throw invalid(requestId, List.of(error(MAIN_SHEET, "2행부터 검토할 조건을 넣어 주세요.")));
        if (table.data.size() > MAX_DATA_ROWS) {
            throw invalid(requestId, List.of(error(MAIN_SHEET, "한 파일에 최대 " + MAX_DATA_ROWS + "행까지 검토할 수 있습니다.")));
        }
        List<Row> rows = new ArrayList<>();
        for (Map.Entry<Integer, List<Object>> entry : table.data.entrySet()) {
            rows.add(row(entry.getKey(), entry.getValue(), table));
        }
        List<Map<String, Object>> sheetErrors = new ArrayList<>();
        if (linkage != null) rows = attachLinkage(rows, linkage, sheetErrors, requestId);
        return new Parsed(rows, sheetErrors);
    }

    private static Row row(int number, List<Object> cells, Table table) {
        List<Map<String, String>> errors = new ArrayList<>();
        ObjectNode source = table.source(cells);
        ObjectNode offer = JsonNodeFactory.instance.objectNode();
        offer.putObject("supplier_size");
        offer.putObject("buyer_size");
        ObjectNode transaction = offer.putObject("transaction");
        for (Column column : MAIN) {
            Integer index = table.columns.get(column);
            Object cell = index == null || index >= cells.size() ? null : cells.get(index);
            convert(target(offer, column.path()), leaf(column.path()), column, cell, errors, column.label());
        }
        for (String company : List.of("supplier_size", "buyer_size")) {
            ObjectNode facts = (ObjectNode) offer.get(company);
            // 관계기업이 있다고 하지 않으면 합산 매출과 검증 값은 쓰지 않는다 (/review 화면과 같은 규칙)
            if (!facts.path("has_related_enterprises").asBoolean(false)) {
                facts.putNull("related_aggregate_revenue_eok");
                facts.put("related_calculation_verified", false);
            }
        }
        transaction.putArray("linkage_details");
        String supplier = offer.path("supplier").isString() ? offer.get("supplier").stringValue() : null;
        String rfq = offer.path("rfq_id").isString() ? offer.get("rfq_id").stringValue() : null;
        return new Row(number, source, errors.isEmpty() ? offer : null, errors, supplier, rfq);
    }

    /** 연동상세 시트 행을 같은 공급자 ID + RFQ ID 거래 행의 transaction.linkage_details에 붙인다. */
    private static List<Row> attachLinkage(List<Row> rows, List<List<Object>> sheet, List<Map<String, Object>> sheetErrors,
                                           UUID requestId) {
        Table table = Table.of(sheet, LINKAGE, LINKAGE_SHEET, requestId);
        if (table.data.size() > MAX_LINKAGE_ROWS) {
            throw invalid(requestId, List.of(error(LINKAGE_SHEET, "연동상세는 최대 " + MAX_LINKAGE_ROWS + "행까지 넣을 수 있습니다.")));
        }
        Map<String, List<Row>> byKey = new HashMap<>();
        for (Row row : rows) {
            if (row.supplierId() != null && row.rfqId() != null) {
                byKey.computeIfAbsent(row.supplierId() + "|" + row.rfqId(), k -> new ArrayList<>()).add(row);
            }
        }
        Map<Integer, List<Map<String, String>>> extraErrors = new HashMap<>();
        Map<Integer, ArrayNode> linked = new HashMap<>();
        Map<Integer, ArrayNode> linkedSource = new HashMap<>();
        for (Map.Entry<Integer, List<Object>> entry : table.data.entrySet()) {
            int number = entry.getKey();
            List<Map<String, String>> errors = new ArrayList<>();
            ObjectNode detail = JsonNodeFactory.instance.objectNode();
            for (Column column : LINKAGE) {
                Integer index = table.columns.get(column);
                Object cell = index == null || index >= entry.getValue().size() ? null : entry.getValue().get(index);
                convert(detail, column.path(), column, cell, errors, LINKAGE_SHEET + " " + number + "행 · " + column.label());
            }
            String key = detail.path("supplier").asString("") + "|" + detail.path("rfq_id").asString("");
            List<Row> targets = byKey.getOrDefault(key, List.of());
            if (targets.size() != 1) {
                sheetErrors.add(Map.<String, Object>of("sheet", LINKAGE_SHEET, "row", number, "message", targets.isEmpty()
                        ? "같은 공급자 ID와 RFQ ID의 거래조건 행이 없습니다."
                        : "같은 공급자 ID와 RFQ ID의 거래조건 행이 여러 개라 연결할 수 없습니다."));
                // 연결이 모호하면 해당 거래 행들이 연동 상세 없이 검토되지 않게 막는다
                for (Row target : targets) {
                    extraErrors.computeIfAbsent(target.rowNumber(), k -> new ArrayList<>())
                            .add(error(LINKAGE_SHEET + " " + number + "행", "같은 공급자 ID와 RFQ ID의 거래조건 행이 여러 개라 연동 상세를 연결할 수 없습니다."));
                }
                continue;
            }
            int target = targets.getFirst().rowNumber();
            linkedSource.computeIfAbsent(target, k -> JsonNodeFactory.instance.arrayNode())
                    .addObject().put("row", number).set("cells", table.source(entry.getValue()));
            if (!errors.isEmpty()) { extraErrors.computeIfAbsent(target, k -> new ArrayList<>()).addAll(errors); continue; }
            detail.remove("supplier");
            detail.remove("rfq_id");
            linked.computeIfAbsent(target, k -> JsonNodeFactory.instance.arrayNode()).add(detail);
        }
        List<Row> out = new ArrayList<>();
        for (Row row : rows) {
            int number = row.rowNumber();
            if (linkedSource.containsKey(number)) row.source().set(LINKAGE_SHEET, linkedSource.get(number));
            List<Map<String, String>> errors = new ArrayList<>(row.errors());
            errors.addAll(extraErrors.getOrDefault(number, List.of()));
            ObjectNode offer = errors.isEmpty() ? row.offer() : null;
            if (offer != null && linked.containsKey(number)) ((ObjectNode) offer.get("transaction")).set("linkage_details", linked.get(number));
            out.add(new Row(number, row.source(), offer, errors, row.supplierId(), row.rfqId()));
        }
        return out;
    }

    /** 머리글을 해석한 시트. 모르는 머리글, 중복 머리글, 빠진 필수 열은 모든 행에 해당하므로 파일 전체를 거절한다. */
    private record Table(Map<Column, Integer> columns, List<String> headers, Map<Integer, List<Object>> data) {
        static Table of(List<List<Object>> sheet, List<Column> definition, String sheetName, UUID requestId) {
            if (sheet.isEmpty() || blank(sheet.getFirst())) throw invalid(requestId, List.of(error(sheetName, "1행에 머리글이 없습니다.")));
            Map<String, Column> lookup = new HashMap<>();
            for (Column column : definition) {
                lookup.put(normalize(column.label()), column);
                for (String alias : column.aliases()) lookup.put(normalize(alias), column);
            }
            List<Map<String, String>> errors = new ArrayList<>();
            Map<Column, Integer> columns = new LinkedHashMap<>();
            List<String> headers = new ArrayList<>();
            List<Object> header = sheet.getFirst();
            for (int i = 0; i < header.size(); i++) {
                String label = header.get(i) == null ? "" : header.get(i).toString().strip();
                headers.add(label);
                if (label.isEmpty()) continue;
                Column column = lookup.get(normalize(label));
                if (column == null) errors.add(error(sheetName + " · " + label, "알 수 없는 머리글입니다."));
                else if (columns.putIfAbsent(column, i) != null) errors.add(error(sheetName + " · " + label, "같은 항목의 머리글이 두 번 있습니다."));
            }
            for (Column column : definition) {
                if (column.required() && !columns.containsKey(column)) errors.add(error(sheetName + " · " + column.label(), "필수 열이 없습니다."));
            }
            if (!errors.isEmpty()) throw invalid(requestId, errors);
            Map<Integer, List<Object>> data = new LinkedHashMap<>();
            for (int r = 1; r < sheet.size(); r++) if (!blank(sheet.get(r))) data.put(r + 1, sheet.get(r));
            return new Table(columns, headers, data);
        }

        /** 입력 원본: 머리글 → 칸 값 그대로. 저장해서 사용자가 올린 값과 변환 값을 대조할 수 있게 한다. */
        ObjectNode source(List<Object> cells) {
            ObjectNode out = JsonNodeFactory.instance.objectNode();
            for (int i = 0; i < headers.size(); i++) {
                if (headers.get(i).isEmpty()) continue;
                Object v = i < cells.size() ? cells.get(i) : null;
                if (v == null) out.putNull(headers.get(i));
                else if (v instanceof Double d) out.put(headers.get(i), d);
                else if (v instanceof Boolean b) out.put(headers.get(i), b);
                else out.put(headers.get(i), v.toString());
            }
            return out;
        }
    }

    private static void convert(ObjectNode target, String field, Column column, Object value,
                                List<Map<String, String>> errors, String label) {
        Object cell = value instanceof String text ? (text.isBlank() ? null : text.strip()) : value;
        try {
            if (cell instanceof XlsxReader.CellError error) throw new IllegalArgumentException("수식 오류(" + error.code() + ")가 있습니다.");
            if (cell == null && column.required()) throw new IllegalArgumentException("값을 입력해 주세요.");
            switch (column.kind()) {
                case TEXT -> { if (cell == null) target.putNull(field); else target.put(field, text(cell)); }
                case SUPPLIER_ID -> target.put(field, id(cell, "SUP-[0-9]{4}", "SUP-0001"));
                case RFQ_ID -> target.put(field, id(cell, "RFQ-[0-9]{4}-[0-9]{3}", "RFQ-2026-001"));
                case POSITIVE_INT -> { if (cell == null) target.putNull(field); else target.put(field, positiveInt(cell)); }
                case RATIO -> { if (cell == null) target.putNull(field); else target.put(field, ratio(cell, 1)); }
                case TRIGGER_RATIO -> { if (cell == null) target.putNull(field); else target.put(field, ratio(cell, 0.1)); }
                case EOK -> { if (cell == null) target.putNull(field); else target.put(field, eok(cell)); }
                case BOOL -> {
                    Boolean flag = flag(cell);
                    if (flag == null) throw new IllegalArgumentException("예 또는 아니오로 입력해 주세요.");
                    target.put(field, flag);
                }
                case BOOL_FALSE -> { Boolean flag = flag(cell); target.put(field, flag != null && flag); }
                case TRI -> { Boolean flag = flag(cell); if (flag == null) target.putNull(field); else target.put(field, flag); }
                case DATE -> { if (cell == null) target.putNull(field); else target.put(field, date(cell)); }
                case LIST -> {
                    ArrayNode list = target.putArray(field);
                    if (cell != null) Arrays.stream(text(cell).split("[,;\\n]")).map(String::strip).filter(s -> !s.isEmpty()).forEach(list::add);
                }
                case ENUM -> {
                    String chosen = cell == null ? column.enumValues().get("모름") : column.enumValues().get(normalize(text(cell)));
                    if (chosen == null) {
                        throw new IllegalArgumentException(String.join("·", column.enumValues().keySet().stream().sorted().toList()) + " 중 하나로 입력해 주세요.");
                    }
                    target.put(field, chosen);
                }
            }
        } catch (IllegalArgumentException e) {
            errors.add(error(label, e.getMessage()));
        }
    }

    private static ObjectNode target(ObjectNode offer, String path) {
        int dot = path.indexOf('.');
        return dot < 0 ? offer : (ObjectNode) offer.get(path.substring(0, dot));
    }

    private static String leaf(String path) { return path.substring(path.indexOf('.') + 1); }

    private static String text(Object cell) {
        if (cell instanceof Double number && number == Math.rint(number)) return String.valueOf(number.longValue());
        String value = cell.toString().strip();
        if (value.length() > 500) throw new IllegalArgumentException("500자 이하로 입력해 주세요.");
        return value;
    }

    private static String id(Object cell, String pattern, String example) {
        String value = text(cell).toUpperCase(Locale.ROOT);
        if (!value.matches(pattern)) throw new IllegalArgumentException(example + " 형식으로 입력해 주세요.");
        return value;
    }

    private static long positiveInt(Object cell) {
        double number = number(cell, "원", "일", "개", "EA", "ea");
        if (number != Math.rint(number) || number <= 0 || number > Integer.MAX_VALUE) {
            throw new IllegalArgumentException("0보다 큰 정수로 입력해 주세요.");
        }
        return (long) number;
    }

    /** 40%, 40, 0.4 모두 40%다. %가 없으면 1 이하는 비율, 1 초과는 퍼센트로 읽는다. */
    private static double ratio(Object cell, double max) {
        boolean percent = cell instanceof String text && text.endsWith("%");
        double number = number(cell, "%");
        double ratio = percent || number > 1 ? number / 100 : number;
        if (ratio < 0 || ratio > max) throw new IllegalArgumentException("0~" + Math.round(max * 100) + "% 사이로 입력해 주세요.");
        return ratio;
    }

    private static double eok(Object cell) {
        double number = number(cell, "억원", "억");
        if (number < 0) throw new IllegalArgumentException("0 이상으로 입력해 주세요.");
        return number;
    }

    private static double number(Object cell, String... units) {
        if (cell instanceof Double number) return number;
        if (cell instanceof Boolean) throw new IllegalArgumentException("숫자로 입력해 주세요.");
        String text = cell.toString().replace(",", "").strip();
        for (String unit : units) text = text.replace(unit, "");
        try {
            double number = Double.parseDouble(text.strip());
            if (!Double.isFinite(number)) throw new NumberFormatException();
            return number;
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("숫자로 입력해 주세요.");
        }
    }

    private static Boolean flag(Object cell) {
        if (cell == null) return null;
        if (cell instanceof Boolean flag) return flag;
        String text = cell instanceof Double number && number == Math.rint(number)
                ? String.valueOf(number.longValue()) : cell.toString().strip().toLowerCase(Locale.ROOT);
        if (TRUE.contains(text)) return true;
        if (FALSE.contains(text)) return false;
        if (UNKNOWN.contains(text)) return null;
        throw new IllegalArgumentException("예, 아니오 또는 모름으로 입력해 주세요.");
    }

    private static String date(Object cell) {
        if (cell instanceof Double serial) {
            if (serial < 1 || serial > 2_958_465 || serial != Math.rint(serial)) throw new IllegalArgumentException("날짜로 입력해 주세요.");
            return EXCEL_EPOCH.plusDays(serial.longValue()).toString();
        }
        try { return LocalDate.parse(cell.toString().strip().replace('.', '-').replace('/', '-')).toString(); }
        catch (DateTimeParseException e) { throw new IllegalArgumentException("YYYY-MM-DD 형식의 실제 날짜로 입력해 주세요."); }
    }

    static String normalize(String header) {
        return header.replaceAll("\\([^)]*\\)|\\[[^]]*]", "").replaceAll("[\\s_\\-·*/.]", "").toLowerCase(Locale.ROOT);
    }

    private static boolean blank(List<Object> row) {
        return row.stream().allMatch(v -> v == null || (v instanceof String s && s.isBlank()));
    }

    private static Map<String, String> error(String column, String message) {
        return Map.of("column", column, "message", message);
    }

    static ApiException invalid(UUID requestId, List<Map<String, String>> errors) {
        return ApiException.of(requestId, 400, "VALIDATION_ERROR", "Invalid legal review workbook", false,
                Map.of("errors", errors));
    }
}
