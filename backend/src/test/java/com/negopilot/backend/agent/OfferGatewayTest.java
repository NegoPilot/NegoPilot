package com.negopilot.backend.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.http.AgentHttpTransport;
import java.net.URI;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Consumer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpMethod;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class OfferGatewayTest {
    private static final UUID ID = UUID.fromString("62751dc6-e6ad-41dc-a7fd-a11f79b5f3c2");
    private static final Set<Integer> STATUSES = Set.of(200, 400, 422, 500, 503, 504);
    private final JsonMapper mapper = JsonMapper.builder().build();
    private AgentHttpTransport transport;
    private GenerationGateway gateway;

    @BeforeEach
    void setup() {
        transport = mock(AgentHttpTransport.class);
        when(transport.uri(any())).thenAnswer(call -> URI.create("http://agent.test" + call.getArgument(0)));
        gateway = new GenerationGateway(transport, mock(GenerationResponseValidator.class));
    }

    @Test
    void proposalBuildsEnvelopeAndAcceptsNoOffer() {
        ObjectNode response = proposal();
        response.putNull("offer_document");
        ((ObjectNode) response.get("check")).putNull("verdict_code");
        stub(200, response);
        ObjectNode offer = mapper.createObjectNode().put("supplier", "SUP-0060");
        assertThat(gateway.propose(ID, offer)).isEqualTo(response);
        ObjectNode sent = sent("/internal/offer-proposals?format=json");
        assertThat(sent.propertyNames()).containsExactlyInAnyOrder("request_id", "offer");
        assertThat(sent.get("request_id").stringValue()).isEqualTo(ID.toString());
        assertThat(sent.get("offer")).isEqualTo(offer);
    }

    @Test
    void proposalRejectsMismatchedIdAndInvalidPublicShape() {
        for (Consumer<ObjectNode> mutate : java.util.List.<Consumer<ObjectNode>>of(
                node -> node.put("request_id", UUID.randomUUID().toString()),
                node -> node.put("offer_document", "invalid"),
                node -> node.putNull("internal_briefing"),
                node -> node.remove("check"),
                node -> ((ObjectNode) node.get("check")).put("warnings", "invalid"),
                node -> ((ObjectNode) node.get("check")).put("verdict_code", "unexpected"))) {
            ObjectNode response = proposal();
            mutate.accept(response);
            stub(200, response);
            assertInvalid(() -> gateway.propose(ID, mapper.createObjectNode()));
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"pdf", "docx", "both"})
    void renderBuildsEnvelopeAndChecksRequestedAttachmentSet(String files) {
        ObjectNode response = rendered(files);
        stub(200, response);
        ObjectNode document = mapper.createObjectNode().put("version", 2).put("unit_price", 1948);
        assertThat(gateway.render(ID, document, files)).isEqualTo(response);
        ObjectNode sent = sent("/internal/offer-proposals/render?format=json");
        assertThat(sent.propertyNames()).containsExactlyInAnyOrder("request_id", "document", "files");
        assertThat(sent.get("document")).isEqualTo(document);
        assertThat(sent.get("files").stringValue()).isEqualTo(files);
    }

    @ParameterizedTest
    @ValueSource(strings = {"hash", "missing", "media", "path", "control", "base64", "empty", "duplicate"})
    void renderRejectsInvalidAttachmentsOrHash(String defect) {
        ObjectNode response = rendered("pdf");
        ObjectNode attachment = (ObjectNode) response.at("/attachments/0");
        switch (defect) {
            case "hash" -> ((ObjectNode) response.get("document")).put("content_hash", "unhashed");
            case "missing" -> response.putArray("attachments");
            case "media" -> attachment.put("content_type", "text/html");
            case "path" -> attachment.put("filename", "../제안서.pdf");
            case "control" -> attachment.put("filename", "제안서\r\n.pdf");
            case "base64" -> attachment.put("data_base64", "%%%invalid");
            case "empty" -> attachment.put("data_base64", "");
            case "duplicate" -> response.withArray("attachments").add(attachment.deepCopy());
        }
        stub(200, response);
        assertInvalid(() -> gateway.render(ID, mapper.createObjectNode(), "pdf"));
    }

    @Test
    void renderRejectsDifferentFileSetAndInvalidRequestedFormat() {
        stub(200, rendered("docx"));
        assertInvalid(() -> gateway.render(ID, mapper.createObjectNode(), "pdf"));
        assertThatThrownBy(() -> gateway.render(ID, mapper.createObjectNode(), "xlsx"))
                .isInstanceOfSatisfying(ApiException.class, ex -> assertThat(ex.code).isEqualTo("VALIDATION_ERROR"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"RFQ_NOT_FOUND", "SUPPLIER_NOT_FOUND", "VALUE_OUT_OF_RANGE", "NO_OFFER"})
    void forwardsBusinessErrorCodes(String code) {
        stub(422, error(code, false));
        assertThatThrownBy(() -> gateway.propose(ID, mapper.createObjectNode()))
                .isInstanceOfSatisfying(ApiException.class, ex -> {
                    assertThat(ex.code).isEqualTo(code);
                    assertThat(ex.status.value()).isEqualTo(422);
                    assertThat(ex.getMessage()).doesNotContain("private");
                });
    }

    @Test
    void violationFindingsBecomeDetailsAndMalformedErrorIs502() {
        ObjectNode response = error("OFFER_VIOLATION", false);
        response.putArray("findings").addObject().put("code", "PAYMENT_DAYS_OVER_60")
                .put("message", "지급 기간 확인").putArray("law_refs");
        stub(422, response);
        assertThatThrownBy(() -> gateway.render(ID, mapper.createObjectNode(), "pdf"))
                .isInstanceOfSatisfying(ApiException.class, ex -> {
                    assertThat(ex.code).isEqualTo("OFFER_VIOLATION");
                    assertThat(ex.details).isInstanceOf(Map.class);
                    assertThat(((Map<?, ?>) ex.details).get("findings")).isEqualTo(response.get("findings"));
                });
        response.remove("findings");
        assertInvalid(() -> gateway.render(ID, mapper.createObjectNode(), "pdf"));
        stub(422, error("INTERNAL_DIAGNOSTIC", false));
        assertInvalid(() -> gateway.propose(ID, mapper.createObjectNode()));
    }

    @ParameterizedTest
    @CsvSource({"400,VALIDATION_ERROR,false", "500,INTERNAL_ERROR,false", "503,UPSTREAM_UNAVAILABLE,true",
            "504,UPSTREAM_TIMEOUT,true"})
    void preservesExistingHttpErrorMapping(int status, String code, boolean retryable) {
        stub(status, error(code, retryable));
        assertThatThrownBy(() -> gateway.propose(ID, mapper.createObjectNode()))
                .isInstanceOfSatisfying(ApiException.class, ex -> {
                    assertThat(ex.status.value()).isEqualTo(status);
                    assertThat(ex.code).isEqualTo(code);
                    assertThat(ex.retryable).isEqualTo(retryable);
                });
    }

    @Test
    void sharedReviewRequestPreservesFactsAndDoesNotMutateSource() {
        ObjectNode offer = (ObjectNode) mapper.readTree("""
                {"supplier":"SUP-0060","rfq_id":"RFQ-2026-002","price_linkage":false,
                 "buyer_size":{"average_revenue_eok":120.01,"independence_met":null},
                 "transaction":{"category":"manufacturing"}}
                """);
        ObjectNode body = new LegalReviewRequestFactory(mapper).create(ID, offer);
        assertThat(body.path("query").stringValue()).isEqualTo("입력한 기업 정보와 계약 조건의 법규 검토");
        assertThat(body.at("/context/selected_supplier_ids").isArray()).isTrue();
        assertThat(body.get("offer")).isEqualTo(offer);
        ((ObjectNode) body.get("offer")).put("unit_price", 1948);
        assertThat(offer.has("unit_price")).isFalse();
    }

    private void stub(int status, JsonNode response) {
        when(transport.exchange(eq(HttpMethod.POST), any(), any(), eq(ID), eq(STATUSES), any()))
                .thenAnswer(call -> ((AgentHttpTransport.ResponseHandler) call.getArgument(5)).handle(status, response));
    }

    private ObjectNode sent(String path) {
        ArgumentCaptor<JsonNode> body = ArgumentCaptor.forClass(JsonNode.class);
        verify(transport).exchange(eq(HttpMethod.POST), eq(URI.create("http://agent.test" + path)),
                body.capture(), eq(ID), eq(STATUSES), any());
        return (ObjectNode) body.getValue();
    }

    private ObjectNode proposal() {
        ObjectNode response = mapper.createObjectNode().put("request_id", ID.toString());
        response.putObject("offer_document");
        response.putObject("internal_briefing").put("status", "offer");
        response.putObject("check").put("verdict_code", "CHECK_NEEDED").putArray("warnings");
        return response;
    }

    private ObjectNode rendered(String files) {
        ObjectNode response = mapper.createObjectNode().put("request_id", ID.toString());
        response.putObject("document").put("content_hash", "sha256:" + "a".repeat(64));
        response.putObject("check").put("verdict_code", "OK").putArray("warnings");
        var attachments = response.putArray("attachments");
        for (String extension : files.equals("both") ? Set.of("pdf", "docx") : Set.of(files)) {
            attachments.addObject().put("filename", "제안서." + extension)
                    .put("content_type", extension.equals("pdf") ? "application/pdf"
                            : "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                    .put("data_base64", "ZmlsZQ==");
        }
        return response;
    }

    private ObjectNode error(String code, boolean retryable) {
        ObjectNode response = mapper.createObjectNode().put("request_id", ID.toString());
        response.putObject("error").put("code", code).put("message", "private diagnostics").put("retryable", retryable);
        return response;
    }

    private void assertInvalid(Runnable action) {
        assertThatThrownBy(action::run).isInstanceOfSatisfying(ApiException.class, ex -> {
            assertThat(ex.code).isEqualTo("UPSTREAM_INVALID_RESPONSE");
            assertThat(ex.status.value()).isEqualTo(502);
        });
    }
}
