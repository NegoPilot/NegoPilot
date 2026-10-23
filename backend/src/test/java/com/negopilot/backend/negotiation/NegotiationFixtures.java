package com.negopilot.backend.negotiation;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

final class NegotiationFixtures {
    static final JsonMapper JSON = JsonMapper.builder().build();
    static final String SUPPLIER = "SUP-0060";
    static final String RFQ = "RFQ-2026-002";
    static final int PRIVATE_MAX_PRICE = 987654321;
    static final String TOKEN = "server-only-review-token";

    private NegotiationFixtures() {}

    static ObjectNode object(String text) { return (ObjectNode) JSON.readTree(text); }

    static ObjectNode reviewInput() {
        return object("""
                {"as_of":"2026-10-23","unit_price":2050,"payment_days":60,"price_linkage":true,
                 "contract_days":365,"raw_material_ratio":0.4200123456789,"energy_ratio":null,
                 "linkage_waived_by_agreement":false,"supplier_size":{},"buyer_size":{},
                 "transaction":{"transaction_type":"manufacturing"}}
                """);
    }

    static ObjectNode reviewResponse(UUID requestId, String assessmentStatus) {
        ObjectNode response = object("""
                {"schema_version":"0.3","request_id":"placeholder","mode":"live",
                 "execution_status":"COMPLETED","generation_status":"COMPLETED",
                 "context":{"selected_supplier_ids":["SUP-0060"]},"error":null,
                 "decomposition":{"schema_version":"0.1","status":"ok","original_query":"검토",
                   "sub_queries":[{"id":"q1","question":"기업 정보 조회","original_text":"검토",
                     "intent":"supplier_detail","grounding":"required","confidence":0.95,
                     "entities":{"supplier":"SUP-0060"},"inputs":[],"depends_on":[]}],
                   "clarification":null,"assumptions":[]},
                 "routing":{"schema_version":"0.1","status":"routed","outcomes":[{"id":"q1",
                   "intent":"supplier_detail","status":"ok","calls":[{"server":"business-mcp",
                     "tool":"get_supplier","args":{"supplier":"SUP-0060"},
                     "result":{"supplier_id":"SUP-0060"},"error":null}],
                   "referenced_articles":[],"missing":[],"message":null}],"halted":false,"halt_reason":null},
                 "answer":{"kind":"answer","text":"거래 조건을 검토했습니다.","citations":[],
                   "options":[],"grounded":true,"notices":[],"evidence":[],"verification":null}}
                """).put("request_id", requestId.toString());
        if (assessmentStatus != null) {
            ObjectNode review = response.putObject("legal_review").put("review_id", "agent-review-1")
                    .put("input_hash", "a".repeat(64)).put("review_token", TOKEN).put("expires_at", 1800000000L);
            review.putObject("assessment").put("status", assessmentStatus).putArray("findings")
                    .add(object("{\"id\":\"supported-1\",\"status\":\"supported\"}"))
                    .add(object("{\"id\":\"unresolved-1\",\"status\":\"needs_information\"}"));
        }
        return response;
    }

    static ObjectNode document() {
        return object("""
                {"schema_version":"1","document_id":"NP-RFQ-2026-002-SUP-0060-test0001","version":1,
                 "status":"draft","issued_on":"2026-10-23","reply_by":"2026-10-30","as_of":"2026-10-23",
                 "supplier_id":"SUP-0060","supplier_name":"온새프레스",
                 "sender":{"company":"구매사","department":"구매팀","contact_name":"담당자",
                   "job_title":"매니저","email":"buyer@example.com"},
                 "title":"단가 및 거래조건 제안","rfq_id":"RFQ-2026-002","part_name":"시트 레일",
                 "vehicle":"NX5","material":"SPFH590","annual_volume":240000,"unit_price":1948,
                 "contract_days":365,"contract_amount":467520000,"payment_days":60,"payment_method":"현금",
                 "price_linkage":true,"linkage_waived_by_agreement":false,"linkage_needs_input":false,
                 "raw_material_ratio":0.4200123456789,"energy_ratio":null,
                 "linkage_terms":{"기준 지표":"","조정 요건(변동률)":"","조정 주기":"","조정 산식":""},
                 "delivery_notes":null,"cover_letter":"거래 조건을 제안합니다.","verdict_code":"OK",
                 "content_hash":null}
                """);
    }

    static ObjectNode proposalResponse(UUID requestId) {
        ObjectNode response = JSON.createObjectNode().put("request_id", requestId.toString());
        response.set("offer_document", document());
        response.set("check", object("{\"verdict_code\":\"OK\",\"warnings\":[]}"));
        ObjectNode briefing = object("""
                {"status":"offer","rfq_id":"RFQ-2026-002","part_name":"시트 레일",
                 "supplier_id":"SUP-0060","supplier_name":"온새프레스","as_of":"2026-10-23",
                 "target_price":1750,"offer":{"unit_price":1948,"payment_days":60,"price_linkage":true,
                   "contract_days":365,"linkage_waived_by_agreement":false,"raw_material_ratio":0.4200123456789,
                   "energy_ratio":null,"annual_volume":240000,"contract_amount":467520000,
                   "notes":null,"verdict_code":"OK","rationale":[],"warnings":[],"law_refs":[],
                   "price_range":[{"name":"opening","unit_price":1948},{"name":"settlement","unit_price":1999},
                     {"name":"walk_away","unit_price":987654321}],
                   "concessions":["내부 양보 순서"],"objections":[{"issue":"내부 쟁점","response":"내부 대응"}]},
                 "writing":{"strategy":"내부 전략","objection_scripts":[],"opening_message":"내부 제안 멘트"},
                 "benchmarks":{"internal_marker":"외부 비공개"},"considered":[],"filled":[],
                 "input_needed":[],"no_offer_reasons":[],"explanation":null}
                """);
        response.set("internal_briefing", briefing);
        return response;
    }

    static ObjectNode noOfferResponse(UUID requestId) {
        ObjectNode response = proposalResponse(requestId);
        response.putNull("offer_document");
        ((ObjectNode) response.get("check")).putNull("verdict_code");
        ((ObjectNode) response.get("internal_briefing")).put("status", "no_offer").putNull("offer");
        return response;
    }

    static ObjectNode renderResponse(UUID requestId, JsonNode document, String files) {
        ObjectNode response = JSON.createObjectNode().put("request_id", requestId.toString());
        response.set("document", ((ObjectNode) document.deepCopy()).put("content_hash", "sha256:" + "b".repeat(64)));
        response.set("check", object("{\"verdict_code\":\"OK\",\"warnings\":[]}"));
        var attachments = response.putArray("attachments");
        for (String format : files.equals("both") ? new String[] {"pdf", "docx"} : new String[] {files}) {
            String name = "제안서_RFQ-2026-002_SUP-0060_v" + document.path("version").intValue() + "." + format;
            String contentType = format.equals("pdf") ? "application/pdf"
                    : "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
            String content = document.path("unit_price").intValue() + ":" + document.path("version").intValue();
            attachments.addObject().put("filename", name).put("content_type", contentType)
                    .put("data_base64", Base64.getEncoder().encodeToString(content.getBytes(StandardCharsets.UTF_8)));
        }
        return response;
    }
}
