package com.negopilot.backend;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.stream.Stream;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import tools.jackson.databind.json.JsonMapper;

import static org.assertj.core.api.Assertions.assertThat;

@org.springframework.test.context.ActiveProfiles("legacy-test")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {"agent.execution-mode=live", "agent.internal-api-key="})
class SupplierChatModeTest {
    private static final HttpClient CLIENT = HttpClient.newHttpClient();
    private static final JsonMapper JSON = JsonMapper.builder().build();
    @LocalServerPort int port;

    static Stream<Arguments> endpoints() {
        String id = "62751dc6-e6ad-41dc-a7fd-a11f79b5f3c2";
        return Stream.of(
                Arguments.of("GET", "/api/v1/business/catalog", ""),
                Arguments.of("GET", "/api/v1/suppliers?process=test", ""),
                Arguments.of("GET", "/api/v1/suppliers/SUP-0034", ""),
                Arguments.of("POST", "/api/v1/conversations", "{\"context\":{\"selected_supplier_ids\":[]}}"),
                Arguments.of("GET", "/api/v1/conversations/" + id, ""),
                Arguments.of("POST", "/api/v1/conversations/" + id + "/messages", "{\"query\":\"test\"}"));
    }

    @ParameterizedTest
    @MethodSource("endpoints")
    void allNewEndpointsRequireStubMode(String method, String path, String body) throws Exception {
        var response = send(method, path, body);
        assertThat(response.statusCode()).isEqualTo(503);
        var error = JSON.readTree(response.body()).path("error");
        assertThat(error.path("code").stringValue()).isEqualTo("DEMO_MODE_REQUIRED");
        assertThat(error.path("retryable").booleanValue()).isFalse();
    }

    @Test
    void originalApiStillUsesItsOwnKeyAndHealthRules() throws Exception {
        var response = send("POST", "/api/v1/agent/queries", "{\"query\":\"test\"}");
        assertThat(response.statusCode()).isEqualTo(503);
        assertThat(JSON.readTree(response.body()).path("error").path("code").stringValue())
                .isEqualTo("UPSTREAM_UNAVAILABLE");
        assertThat(send("GET", "/health", "").statusCode()).isEqualTo(200);
    }

    private HttpResponse<String> send(String method, String path, String body) throws Exception {
        return CLIENT.send(HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path))
                .timeout(Duration.ofSeconds(5)).header("Content-Type", "application/json")
                .method(method, HttpRequest.BodyPublishers.ofString(body)).build(), HttpResponse.BodyHandlers.ofString());
    }

    @AfterAll
    static void close() { CLIENT.close(); }
}
