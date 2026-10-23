package com.negopilot.backend.legalreview;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.parsers.ParserConfigurationException;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;
import org.xml.sax.SAXException;

/**
 * .xlsx 시트를 JDK만으로 읽는다 (zip + DOM). 칸 값은 String, Double, Boolean 또는 null이다.
 * 서식은 읽지 않으므로 날짜 칸은 엑셀 일련번호(Double)로 온다.
 */
final class XlsxReader {
    private static final String MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
    private static final String RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
    private static final String PACKAGE_RELS = "http://schemas.openxmlformats.org/package/2006/relationships";
    // 조건 시트는 수십 KB 안쪽이다. 압축 폭탄과 지나치게 큰 시트를 미리 막는다.
    private static final long MAX_UNZIPPED_BYTES = 16L * 1024 * 1024;
    static final int MAX_ROWS = 1000;
    static final int MAX_COLUMNS = 200;

    static final class InvalidWorkbookException extends Exception {
        InvalidWorkbookException(String message) { super(message); }
    }

    private XlsxReader() { }

    /** 시트 이름 → 행 목록. 통합 문서의 시트 순서를 지킨다. */
    static Map<String, List<List<Object>>> sheets(byte[] data) throws InvalidWorkbookException {
        Map<String, byte[]> parts = unzip(data);
        Map<String, String> targets = new HashMap<>();
        NodeList relations = xml(required(parts, "xl/_rels/workbook.xml.rels")).getElementsByTagNameNS(PACKAGE_RELS, "Relationship");
        for (int i = 0; i < relations.getLength(); i++) {
            Element relation = (Element) relations.item(i);
            targets.put(relation.getAttribute("Id"), relation.getAttribute("Target"));
        }
        List<String> shared = sharedStrings(parts.get("xl/sharedStrings.xml"));
        Map<String, List<List<Object>>> sheets = new LinkedHashMap<>();
        NodeList nodes = xml(required(parts, "xl/workbook.xml")).getElementsByTagNameNS(MAIN, "sheet");
        for (int i = 0; i < nodes.getLength(); i++) {
            Element sheet = (Element) nodes.item(i);
            String target = targets.get(sheet.getAttributeNS(RELS, "id"));
            if (target == null) throw new InvalidWorkbookException("시트를 찾지 못했습니다.");
            String path = target.startsWith("/") ? target.substring(1) : "xl/" + target;
            sheets.put(sheet.getAttribute("name").strip(), rows(xml(required(parts, path)), shared));
        }
        if (sheets.isEmpty()) throw new InvalidWorkbookException("시트가 없습니다.");
        return sheets;
    }

    private static List<List<Object>> rows(Document sheet, List<String> shared) throws InvalidWorkbookException {
        List<List<Object>> rows = new ArrayList<>();
        NodeList rowNodes = sheet.getElementsByTagNameNS(MAIN, "row");
        for (int r = 0; r < rowNodes.getLength(); r++) {
            Element row = (Element) rowNodes.item(r);
            int rowIndex = row.hasAttribute("r") ? index(row.getAttribute("r")) - 1 : rows.size();
            if (rowIndex < 0) throw new InvalidWorkbookException("행 번호를 읽지 못했습니다.");
            if (rowIndex >= MAX_ROWS) throw new InvalidWorkbookException("행이 너무 많습니다.");
            while (rows.size() <= rowIndex) rows.add(new ArrayList<>());
            List<Object> cells = rows.get(rowIndex);
            NodeList cellNodes = row.getElementsByTagNameNS(MAIN, "c");
            for (int c = 0; c < cellNodes.getLength(); c++) {
                Element cell = (Element) cellNodes.item(c);
                int column = cell.hasAttribute("r") ? column(cell.getAttribute("r")) : cells.size();
                if (column >= MAX_COLUMNS) throw new InvalidWorkbookException("열이 너무 많습니다.");
                while (cells.size() <= column) cells.add(null);
                cells.set(column, value(cell, shared));
            }
        }
        return rows;
    }

    private static Object value(Element cell, List<String> shared) throws InvalidWorkbookException {
        Element v = first(cell, "v");
        String raw = v == null ? null : v.getTextContent();
        switch (cell.getAttribute("t")) {
            case "inlineStr" -> {
                Element is = first(cell, "is");
                return is == null ? null : text(is);
            }
            case "s" -> {
                if (raw == null) return null;
                int index = index(raw.trim());
                if (index >= shared.size()) throw new InvalidWorkbookException("공유 문자열 참조가 잘못되었습니다.");
                return shared.get(index);
            }
            case "str" -> { return raw; }
            case "b" -> { return raw == null ? null : raw.trim().equals("1"); }
            case "e" -> { return new CellError(raw == null ? "#ERROR" : raw.trim()); }
            default -> {
                if (raw == null || raw.isBlank()) return null;
                try { return Double.parseDouble(raw.trim()); }
                catch (NumberFormatException e) { throw new InvalidWorkbookException("숫자 칸을 읽지 못했습니다."); }
            }
        }
    }

    /** 수식 오류 칸(#N/A 등). 해당 칸의 행만 오류로 처리한다. */
    record CellError(String code) {
        @Override public String toString() { return code; }
    }

    private static List<String> sharedStrings(byte[] part) throws InvalidWorkbookException {
        List<String> values = new ArrayList<>();
        if (part == null) return values;
        NodeList items = xml(part).getElementsByTagNameNS(MAIN, "si");
        for (int i = 0; i < items.getLength(); i++) values.add(text((Element) items.item(i)));
        return values;
    }

    /** 서식 있는 텍스트 조각을 잇는다. 윗주(rPh)는 뺀다. */
    private static String text(Element parent) {
        StringBuilder out = new StringBuilder();
        NodeList texts = parent.getElementsByTagNameNS(MAIN, "t");
        for (int i = 0; i < texts.getLength(); i++) {
            Node node = texts.item(i);
            if (!"rPh".equals(node.getParentNode().getLocalName())) out.append(node.getTextContent());
        }
        return out.toString();
    }

    private static int column(String reference) throws InvalidWorkbookException {
        int column = 0, i = 0;
        while (i < reference.length() && Character.isLetter(reference.charAt(i))) {
            column = column * 26 + (Character.toUpperCase(reference.charAt(i)) - 'A' + 1);
            if (column > MAX_COLUMNS) throw new InvalidWorkbookException("열이 너무 많습니다.");
            i++;
        }
        if (column == 0) throw new InvalidWorkbookException("칸 위치를 읽지 못했습니다.");
        return column - 1;
    }

    private static int index(String value) throws InvalidWorkbookException {
        if (!value.matches("[0-9]{1,7}")) throw new InvalidWorkbookException("칸 위치를 읽지 못했습니다.");
        return Integer.parseInt(value);
    }

    private static Element first(Element parent, String name) {
        NodeList nodes = parent.getElementsByTagNameNS(MAIN, name);
        return nodes.getLength() == 0 ? null : (Element) nodes.item(0);
    }

    private static byte[] required(Map<String, byte[]> parts, String name) throws InvalidWorkbookException {
        byte[] part = parts.get(name);
        if (part == null) throw new InvalidWorkbookException("엑셀(.xlsx) 파일이 아닙니다.");
        return part;
    }

    private static Map<String, byte[]> unzip(byte[] data) throws InvalidWorkbookException {
        Map<String, byte[]> parts = new HashMap<>();
        long total = 0;
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(data))) {
            ZipEntry entry;
            byte[] buffer = new byte[8192];
            while ((entry = zip.getNextEntry()) != null) {
                String name = entry.getName();
                boolean wanted = name.equals("xl/workbook.xml") || name.equals("xl/_rels/workbook.xml.rels")
                        || name.equals("xl/sharedStrings.xml") || name.startsWith("xl/worksheets/");
                if (!wanted || entry.isDirectory()) continue;
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                int read;
                while ((read = zip.read(buffer)) > 0) {
                    total += read;
                    if (total > MAX_UNZIPPED_BYTES) throw new InvalidWorkbookException("파일 내용이 너무 큽니다.");
                    out.write(buffer, 0, read);
                }
                parts.put(name, out.toByteArray());
            }
        } catch (IOException e) {
            throw new InvalidWorkbookException("엑셀(.xlsx) 파일을 열지 못했습니다.");
        }
        if (parts.isEmpty()) throw new InvalidWorkbookException("엑셀(.xlsx) 파일이 아닙니다.");
        return parts;
    }

    private static Document xml(byte[] part) throws InvalidWorkbookException {
        try (InputStream in = new ByteArrayInputStream(part)) {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            factory.setNamespaceAware(true);
            factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            factory.setXIncludeAware(false);
            factory.setExpandEntityReferences(false);
            return factory.newDocumentBuilder().parse(in);
        } catch (ParserConfigurationException | IOException | SAXException e) {
            throw new InvalidWorkbookException("엑셀(.xlsx) 파일을 읽지 못했습니다.");
        }
    }
}
