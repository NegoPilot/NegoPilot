package com.negopilot.backend.legalreview;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/** 테스트용 최소 .xlsx. 문자열은 sharedStrings, 숫자·논리값은 형식 그대로 쓴다. */
final class TestXlsx {
    private TestXlsx() { }

    /** ISO 8601 날짜 칸(t="d"). 일부 도구가 날짜를 이렇게 저장한다. */
    record IsoDate(String value) { }

    static byte[] of(List<List<Object>> rows) {
        Map<String, List<List<Object>>> sheets = new LinkedHashMap<>();
        sheets.put("거래조건", rows);
        return of(sheets);
    }

    static byte[] of(Map<String, List<List<Object>>> sheets) {
        List<String> shared = new ArrayList<>();
        Map<String, String> parts = new LinkedHashMap<>();
        StringBuilder workbook = new StringBuilder("<?xml version=\"1.0\"?><workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\""
                + " xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><sheets>");
        StringBuilder rels = new StringBuilder("<?xml version=\"1.0\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">");
        int n = 0;
        for (var entry : sheets.entrySet()) {
            n++;
            workbook.append("<sheet name=\"").append(escape(entry.getKey())).append("\" sheetId=\"").append(n).append("\" r:id=\"rId").append(n).append("\"/>");
            rels.append("<Relationship Id=\"rId").append(n).append("\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet")
                    .append(n).append(".xml\"/>");
            parts.put("xl/worksheets/sheet" + n + ".xml", sheet(entry.getValue(), shared));
        }
        workbook.append("</sheets></workbook>");
        rels.append("</Relationships>");
        StringBuilder strings = new StringBuilder("<?xml version=\"1.0\"?><sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">");
        for (String s : shared) strings.append("<si><t>").append(escape(s)).append("</t></si>");
        strings.append("</sst>");
        parts.put("xl/workbook.xml", workbook.toString());
        parts.put("xl/_rels/workbook.xml.rels", rels.toString());
        parts.put("xl/sharedStrings.xml", strings.toString());
        return zip(parts);
    }

    private static String sheet(List<List<Object>> rows, List<String> shared) {
        StringBuilder sheet = new StringBuilder("<?xml version=\"1.0\"?><worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>");
        for (int r = 0; r < rows.size(); r++) {
            sheet.append("<row r=\"").append(r + 1).append("\">");
            List<Object> row = rows.get(r);
            for (int c = 0; c < row.size(); c++) {
                Object value = row.get(c);
                if (value == null) continue;
                String ref = column(c) + (r + 1);
                if (value instanceof IsoDate date) sheet.append("<c r=\"").append(ref).append("\" t=\"d\"><v>").append(escape(date.value())).append("</v></c>");
                else if (value instanceof Boolean flag) sheet.append("<c r=\"").append(ref).append("\" t=\"b\"><v>").append(flag ? 1 : 0).append("</v></c>");
                else if (value instanceof Number number) sheet.append("<c r=\"").append(ref).append("\"><v>").append(number).append("</v></c>");
                else {
                    shared.add(value.toString());
                    sheet.append("<c r=\"").append(ref).append("\" t=\"s\"><v>").append(shared.size() - 1).append("</v></c>");
                }
            }
            sheet.append("</row>");
        }
        return sheet.append("</sheetData></worksheet>").toString();
    }

    static byte[] zip(Map<String, String> parts) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(out)) {
            for (var part : parts.entrySet()) {
                zip.putNextEntry(new ZipEntry(part.getKey()));
                zip.write(part.getValue().getBytes(StandardCharsets.UTF_8));
                zip.closeEntry();
            }
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
        return out.toByteArray();
    }

    private static String column(int index) {
        StringBuilder name = new StringBuilder();
        for (int i = index + 1; i > 0; i = (i - 1) / 26) name.insert(0, (char) ('A' + (i - 1) % 26));
        return name.toString();
    }

    private static String escape(String value) {
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }
}
