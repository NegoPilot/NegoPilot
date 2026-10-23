package com.negopilot.backend.negotiation;

import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

final class NegotiationPureFixtures {
    static final JsonMapper JSON = JsonMapper.builder().build();

    static ObjectNode object(String source) { return (ObjectNode) JSON.readTree(source); }

    static ObjectNode revision() {
        return object("""
                {"revision":1,"base_revision":null,"origin":"GENERATED",
                 "unit_price":1000,"payment_days":60,"price_linkage":true,"contract_days":365,
                 "linkage_waived_by_agreement":false,"raw_material_ratio":0.123456789123,"energy_ratio":null,
                 "annual_volume":10000,"as_of":"2026-10-23",
                 "terms_rev":1,"internal_content_rev":1,"external_content_rev":1,
                 "changed_scopes":[],"change_set":[],
                 "internal_body":{"target_price":900,"benchmarks":{"max_price":937291},
                   "offer":{"contract_amount":10000000,"verdict_code":"CHECK_NEEDED","notes":null,
                     "warnings":["내부 경고"],"rationale":[],"concessions":["기존 순서"],
                     "objections":[{"issue":"기존 쟁점","response":"기존 대응"}],
                     "price_range":[{"name":"opening","unit_price":1000,"vs_target_pct":11.1},
                       {"name":"settlement","unit_price":1100,"verdict_code":"OK"},
                       {"name":"walk_away","unit_price":937291,"verdict_code":"OK"}]},
                   "writing":{"strategy":"기존 전략","opening_message":"인사","objection_scripts":["대응"]}},
                 "external_body":{"schema_version":"1","title":"거래조건 제안","issued_on":"2026-10-23",
                   "reply_by":"2026-10-30","supplier_name":"협력사","part_name":"부품","vehicle":null,
                   "material":null,"payment_method":"현금","linkage_needs_input":false,
                   "linkage_terms":{"기준 지표":"","조정 요건(변동률)":"","조정 주기":"","조정 산식":""},
                   "delivery_notes":null,"cover_letter":"검토를 부탁드립니다",
                   "sender":{"company":"구매사","department":"구매","contact_name":"담당자","email":"a@example.com"}}}
                """);
    }

    static ObjectNode workspace() { return object("{\"supplier_id\":\"SUP-0001\",\"rfq_id\":\"RFQ-2026-001\"}"); }
    static ObjectNode proposal() {
        return object("""
                {"id":"cf4c057a-c347-402c-bae7-b5b1e8abe77a","mail_document_id":"NP-RFQ-2026-001-SUP-0001-cf4c057a"}
                """);
    }
}
