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
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpMethod;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class MailGatewayTest {
    private static final UUID ID = UUID.fromString("0b3c1a52-6f0e-4c55-9d7e-0f4a2d3c9b11");
    private static final Set<Integer> STATUSES = Set.of(200, 400, 422, 500, 503, 504);
    private final JsonMapper mapper = JsonMapper.builder().build();
    private AgentHttpTransport transport;
    private MailGateway gateway;

    @BeforeEach
    void setup() {
        transport = mock(AgentHttpTransport.class);
        when(transport.uri(any())).thenAnswer(call -> URI.create("http://agent.test" + call.getArgument(0)));
        gateway = new MailGateway(transport);
    }

    @Test
    void previewSendsOnlyGivenFieldsAndReturnsResult() {
        stub(HttpMethod.POST, 200, envelope(preview()));
        ObjectNode document = mapper.createObjectNode().put("document_id", "NP-1");
        assertThat(gateway.preview(ID, document, mapper.createArrayNode(), null, null)).isEqualTo(preview());
        ObjectNode sent = sent(HttpMethod.POST, "/internal/offer-mails/preview");
        assertThat(sent.propertyNames()).containsExactlyInAnyOrder("request_id", "document", "attachments");
    }

    @Test
    void sendCarriesTokenRequesterAndClientRef() {
        stub(HttpMethod.POST, 200, envelope(mapper.createObjectNode().put("send_id", "s-1").put("status", "SENT").putNull("message_id")));
        gateway.send(ID, mapper.createObjectNode(), mapper.createArrayNode(), 7, "안녕하세요", "tok", "user-1", "ref-1");
        ObjectNode sent = sent(HttpMethod.POST, "/internal/offer-mails/send");
        assertThat(sent.path("confirmation_token").asText()).isEqualTo("tok");
        assertThat(sent.path("requested_by").asText()).isEqualTo("user-1");
        assertThat(sent.path("client_ref").asText()).isEqualTo("ref-1");
        assertThat(sent.path("contact_id").longValue()).isEqualTo(7);
        assertThat(sent.path("message").asText()).isEqualTo("안녕하세요");
    }

    @Test
    void previewRejectsMissingTokenOrMismatchedRequest() {
        ObjectNode noToken = preview(); noToken.remove("confirmation_token");
        stub(HttpMethod.POST, 200, envelope(noToken));
        assertThatThrownBy(() -> gateway.preview(ID, mapper.createObjectNode(), mapper.createArrayNode(), null, null))
                .isInstanceOfSatisfying(ApiException.class, ex -> assertThat(ex.code).isEqualTo("UPSTREAM_INVALID_RESPONSE"));
        ObjectNode other = envelope(preview()).put("request_id", UUID.randomUUID().toString());
        stub(HttpMethod.POST, 200, other);
        assertThatThrownBy(() -> gateway.preview(ID, mapper.createObjectNode(), mapper.createArrayNode(), null, null))
                .isInstanceOfSatisfying(ApiException.class, ex -> assertThat(ex.code).isEqualTo("UPSTREAM_INVALID_RESPONSE"));
    }

    @ParameterizedTest
    @CsvSource({"422,RECIPIENT_MISSING,422,RECIPIENT_MISSING", "422,CLIENT_REF_USED,422,CLIENT_REF_USED",
            "422,UNKNOWN_CODE,502,UPSTREAM_INVALID_RESPONSE", "503,UPSTREAM_UNAVAILABLE,503,UPSTREAM_UNAVAILABLE",
            "504,UPSTREAM_TIMEOUT,504,UPSTREAM_TIMEOUT"})
    void errorsMapToApiCodes(int status, String code, int expectedStatus, String expectedCode) {
        ObjectNode error = mapper.createObjectNode().put("request_id", ID.toString());
        error.putObject("error").put("code", code).put("message", "거절").put("retryable", status != 422);
        stub(HttpMethod.POST, status, error);
        assertThatThrownBy(() -> gateway.send(ID, mapper.createObjectNode(), mapper.createArrayNode(), 7, null, "t", "u", "r"))
                .isInstanceOfSatisfying(ApiException.class, ex -> {
                    assertThat(ex.code).isEqualTo(expectedCode);
                    assertThat(ex.status.value()).isEqualTo(expectedStatus);
                });
    }

    @Test
    void historyQueriesByDocumentAndClientRef() {
        JsonNode rows = mapper.readTree("[{\"send_id\":\"s-1\",\"status\":\"SENT\"}]");
        ObjectNode response = mapper.createObjectNode().put("request_id", UUID.randomUUID().toString());
        response.set("result", rows);
        stub(HttpMethod.GET, 200, response);
        assertThat(gateway.history(ID, "NP-1", "ref-1")).isEqualTo(rows);
        verify(transport).exchange(eq(HttpMethod.GET), eq(URI.create("http://agent.test/internal/offer-mails?document_id=NP-1&client_ref=ref-1")),
                any(), eq(ID), eq(STATUSES), any());
    }

    private ObjectNode preview() {
        ObjectNode result = mapper.createObjectNode();
        result.putObject("to").put("contact_id", 7).put("name", "김영업").put("email", "s@example.test").put("supplier_id", "SUP-0060");
        result.put("from", "구매팀 <p@x>").put("subject", "제안서").put("body", "본문").put("content_hash", "sha256:" + "a".repeat(64))
                .put("dry_run", true).put("confirmation_token", "tok").put("expires_at", "2026-10-23T08:15:00+00:00");
        result.putArray("attachments");
        return result;
    }
    private ObjectNode envelope(JsonNode result) {
        ObjectNode response = mapper.createObjectNode().put("request_id", ID.toString());
        response.set("result", result);
        return response;
    }
    private void stub(HttpMethod method, int status, JsonNode response) {
        when(transport.exchange(eq(method), any(), any(), eq(ID), eq(STATUSES), any()))
                .thenAnswer(call -> ((AgentHttpTransport.ResponseHandler) call.getArgument(5)).handle(status, response));
    }
    private ObjectNode sent(HttpMethod method, String path) {
        ArgumentCaptor<JsonNode> body = ArgumentCaptor.forClass(JsonNode.class);
        verify(transport).exchange(eq(method), eq(URI.create("http://agent.test" + path)), body.capture(), eq(ID), eq(STATUSES), any());
        return (ObjectNode) body.getValue();
    }
}
