package com.negopilot.backend.common.api;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.authentication;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.negopilot.backend.agent.AgentHttpConfiguration;
import com.negopilot.backend.auth.AuthSessionService;
import com.negopilot.backend.auth.MembershipRepository;
import com.negopilot.backend.auth.SecurityConfiguration;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.MediaType;
import org.springframework.http.ProblemDetail;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.ErrorResponseException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.context.WebApplicationContext;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.servlet.resource.NoResourceFoundException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@WebMvcTest(controllers = ApiErrorHandlerHttpTest.ErrorProbeController.class)
@ActiveProfiles("api-error-handler-http-test")
@Import({SecurityConfiguration.class, ApiErrorHandler.class,
        ApiErrorHandlerHttpTest.ErrorProbeController.class, ApiErrorHandlerHttpTest.JsonConfiguration.class})
class ApiErrorHandlerHttpTest {
    private static final String ROOT = "/api/error-handler-test";
    private static final String MISSING = "/api/no-such-api-private-route";
    private static final String PRIVATE_DETAIL = "private-database-password-and-exception-detail";
    private static final UUID REQUEST_ID = UUID.fromString("6db23fa3-40eb-455d-ad6a-584b4f7eeb82");

    @Autowired WebApplicationContext context;
    @Autowired JsonMapper mapper;
    @MockitoBean MembershipRepository members;
    private MockMvc mvc;

    @TestConfiguration(proxyBeanMethods = false)
    @Profile("api-error-handler-http-test")
    @EnableWebSecurity
    static class JsonConfiguration {
        @Bean JsonMapper contractJsonMapper() {
            return new AgentHttpConfiguration().contractJsonMapper();
        }
    }

    @BeforeEach
    void setup() {
        mvc = MockMvcBuilders.webAppContextSetup(context).apply(springSecurity()).build();
    }

    @ParameterizedTest
    @ValueSource(strings = {"USER", "ADMIN"})
    void authenticatedMissingApiIs404WithSafeEnvelope(String role) throws Exception {
        var result = mvc.perform(get(MISSING).with(principal(role, "ACTIVE")))
                .andExpect(status().isNotFound()).andReturn();

        assertThat(result.getResolvedException()).isInstanceOf(NoResourceFoundException.class);
        assertSafeEnvelope(result, null);
        assertThat(result.getResponse().getContentAsString()).doesNotContain("no-such-api-private-route");
    }

    @Test
    void activeAdminCanReachMissingAdminApiAndGets404() throws Exception {
        var result = mvc.perform(get("/api/admin/no-such-api").with(principal("ADMIN", "ACTIVE")))
                .andExpect(status().isNotFound()).andReturn();
        assertThat(result.getResolvedException()).isInstanceOf(NoResourceFoundException.class);
        assertSafeEnvelope(result, null);
    }

    @Test
    void authenticatedWrongMethodWithCsrfIs405AndKeepsAllow() throws Exception {
        var result = mvc.perform(post(ROOT + "/get-only").with(principal("USER", "ACTIVE"))
                        .with(csrf().asHeader()).requestAttr("request_id", REQUEST_ID))
                .andExpect(status().isMethodNotAllowed())
                .andExpect(header().string(HttpHeaders.ALLOW, "GET"))
                .andReturn();
        assertThat(result.getResolvedException()).isInstanceOf(HttpRequestMethodNotSupportedException.class);
        assertSafeEnvelope(result, REQUEST_ID);
    }

    @Test
    void anonymousMissingApiStillRequiresAuthentication() throws Exception {
        var result = mvc.perform(get(MISSING).requestAttr("request_id", REQUEST_ID))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("UNAUTHENTICATED")).andReturn();
        assertThat(result.getResolvedException()).isNull();
        assertSafeEnvelope(result, REQUEST_ID);
    }

    @ParameterizedTest
    @CsvSource({"USER,ACTIVE", "ADMIN,PENDING"})
    void unauthorizedAdminAccessRemains403BeforeRouteLookup(String role, String membershipStatus) throws Exception {
        var result = mvc.perform(get("/api/admin/no-such-api").with(principal(role, membershipStatus))
                        .requestAttr("request_id", REQUEST_ID))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("FORBIDDEN")).andReturn();
        assertThat(result.getResolvedException()).isNull();
        assertSafeEnvelope(result, REQUEST_ID);
    }

    @Test
    void pendingMemberCannotReachMissingApi() throws Exception {
        mvc.perform(get(MISSING).with(principal("USER", "PENDING")))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.error.code").value("FORBIDDEN"));
    }

    @ParameterizedTest
    @ValueSource(strings = {MISSING, ROOT + "/get-only"})
    void missingCsrfRemains403BeforeRouteAndMethodResolution(String path) throws Exception {
        var result = mvc.perform(post(path).with(principal("USER", "ACTIVE"))
                        .requestAttr("request_id", REQUEST_ID))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("CSRF_INVALID")).andReturn();
        assertThat(result.getResolvedException()).isNull();
        assertSafeEnvelope(result, REQUEST_ID);
    }

    @ParameterizedTest
    @ValueSource(strings = {MISSING, ROOT + "/get-only"})
    void invalidCsrfRemains403BeforeRouteAndMethodResolution(String path) throws Exception {
        var result = mvc.perform(post(path).with(principal("USER", "ACTIVE"))
                        .with(csrf().useInvalidToken().asHeader()).requestAttr("request_id", REQUEST_ID))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("CSRF_INVALID")).andReturn();
        assertThat(result.getResolvedException()).isNull();
        assertSafeEnvelope(result, REQUEST_ID);
    }

    @Test
    void anonymousPostWithoutCsrfStillReturnsCsrf403() throws Exception {
        mvc.perform(post(MISSING)).andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("CSRF_INVALID"));
    }

    @Test
    void anonymousWrongMethodWithCsrfStillReturns401() throws Exception {
        mvc.perform(post(ROOT + "/get-only").with(csrf().asHeader()))
                .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.error.code").value("UNAUTHENTICATED"));
    }

    @ParameterizedTest
    @ValueSource(ints = {400, 404, 409, 502, 503, 504})
    void apiExceptionKeepsDeclaredStatusAndPublicContract(int code) throws Exception {
        var result = mvc.perform(get(ROOT + "/api-exception/" + code).with(principal("USER", "ACTIVE"))
                        .requestAttr("request_id", UUID.randomUUID()))
                .andExpect(status().is(code))
                .andExpect(jsonPath("$.error.code").value("EXISTING_PUBLIC_ERROR"))
                .andExpect(jsonPath("$.error.message").value("Existing safe public message"))
                .andExpect(jsonPath("$.error.retryable").value(code >= 500)).andReturn();
        assertSafeEnvelope(result, REQUEST_ID);
    }

    @Test
    void unexpectedInternalExceptionIsSanitized500() throws Exception {
        var result = mvc.perform(get(ROOT + "/unexpected").with(principal("USER", "ACTIVE"))
                        .requestAttr("request_id", REQUEST_ID))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.error.code").value("INTERNAL_ERROR"))
                .andExpect(jsonPath("$.error.message").value("Internal server error"))
                .andExpect(jsonPath("$.error.retryable").value(false)).andReturn();
        assertSafeEnvelope(result, REQUEST_ID);
    }

    @ParameterizedTest
    @ValueSource(ints = {429, 503, 499})
    void responseStatusExceptionKeepsStatusAndSafeEnvelope(int code) throws Exception {
        var result = mvc.perform(get(ROOT + "/http-exception/" + code).with(principal("USER", "ACTIVE"))
                        .requestAttr("request_id", REQUEST_ID))
                .andExpect(status().is(code)).andReturn();
        assertSafeEnvelope(result, REQUEST_ID);
        if (code == 429) {
            assertThat(result.getResponse().getHeader(HttpHeaders.RETRY_AFTER)).isEqualTo("120");
            assertThat(result.getResponse().getContentType()).isEqualTo(MediaType.APPLICATION_JSON_VALUE);
            String contentLength = result.getResponse().getHeader(HttpHeaders.CONTENT_LENGTH);
            if (contentLength != null) {
                assertThat(Integer.parseInt(contentLength)).isEqualTo(result.getResponse().getContentAsByteArray().length);
            }
        }
    }

    @Test
    void genericSpringErrorResponseKeepsStatusAndHidesProblemDetail() throws Exception {
        var result = mvc.perform(get(ROOT + "/generic-http-exception").with(principal("USER", "ACTIVE"))
                        .requestAttr("request_id", REQUEST_ID))
                .andExpect(status().is(422)).andReturn();
        assertThat(result.getResolvedException()).isExactlyInstanceOf(ErrorResponseException.class);
        assertSafeEnvelope(result, REQUEST_ID);
    }

    @Test
    void wrappedResponseStatusExceptionKeepsStatusAndHeaders() throws Exception {
        var result = mvc.perform(get(ROOT + "/wrapped-http-exception").with(principal("USER", "ACTIVE"))
                        .requestAttr("request_id", REQUEST_ID))
                .andExpect(status().isTooManyRequests())
                .andExpect(header().string(HttpHeaders.RETRY_AFTER, "120")).andReturn();
        assertSafeEnvelope(result, REQUEST_ID);
    }

    @ParameterizedTest
    @ValueSource(strings = {"annotated-value", "annotated-code"})
    void responseStatusAnnotationKeeps410WithoutExposingReason(String endpoint) throws Exception {
        var result = mvc.perform(get(ROOT + "/" + endpoint).with(principal("USER", "ACTIVE"))
                        .requestAttr("request_id", REQUEST_ID))
                .andExpect(status().isGone()).andReturn();
        assertSafeEnvelope(result, REQUEST_ID);
    }

    @ParameterizedTest
    @ValueSource(strings = {"wrapped-annotated-value", "wrapped-annotated-code"})
    void wrappedResponseStatusAnnotationKeeps410WithoutExposingReason(String endpoint) throws Exception {
        var result = mvc.perform(get(ROOT + "/" + endpoint).with(principal("USER", "ACTIVE"))
                        .requestAttr("request_id", REQUEST_ID))
                .andExpect(status().isGone()).andReturn();
        assertSafeEnvelope(result, REQUEST_ID);
    }

    @ParameterizedTest
    @CsvSource({"application/json,{", "text/plain,private-invalid-body"})
    void malformedJsonAndUnsupportedMediaKeepExisting400Contract(String contentType, String body) throws Exception {
        var result = mvc.perform(post(ROOT + "/json-body").with(principal("USER", "ACTIVE"))
                        .with(csrf().asHeader()).requestAttr("request_id", REQUEST_ID)
                        .contentType(contentType).content(body))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.error.message").value("Invalid query request"))
                .andExpect(jsonPath("$.error.retryable").value(false)).andReturn();
        // This legacy validation path intentionally does not propagate request attributes.
        assertSafeEnvelope(result, null);
        assertThat(result.getResponse().getContentAsString()).doesNotContain("private-invalid-body");
    }

    private JsonNode assertSafeEnvelope(MvcResult result, UUID requestId) throws Exception {
        var response = result.getResponse();
        assertThat(MediaType.parseMediaType(response.getContentType()).isCompatibleWith(MediaType.APPLICATION_JSON)).isTrue();
        var body = mapper.readTree(response.getContentAsString());
        assertThat(body.propertyNames()).containsExactlyInAnyOrder("request_id", "error");
        if (requestId == null) {
            assertThat(body.get("request_id").isNull()).isTrue();
        } else {
            assertThat(body.get("request_id").stringValue()).isEqualTo(requestId.toString());
        }
        var error = body.get("error");
        assertThat(error.propertyNames()).containsExactlyInAnyOrder("code", "message", "retryable");
        assertThat(error.get("code").stringValue()).isNotBlank();
        assertThat(error.get("message").stringValue()).isNotBlank();
        assertThat(error.get("retryable").isBoolean()).isTrue();
        assertThat(response.getContentAsString()).doesNotContain(PRIVATE_DETAIL, "stackTrace", "stack_trace",
                "java.lang.IllegalStateException", "ResponseStatusException", "NoResourceFoundException");
        return body;
    }

    private RequestPostProcessor principal(String role, String membershipStatus) {
        UUID id = UUID.randomUUID();
        Instant now = Instant.now();
        var member = new MembershipRepository.Member(id, "member@example.com", "회원", "구매", "매니저", "unused",
                role, membershipStatus, now, now);
        when(members.byId(id)).thenReturn(member);
        return authentication(AuthSessionService.authentication(member.identity()));
    }

    @RestController
    @Profile("api-error-handler-http-test")
    static class ErrorProbeController {
        @GetMapping(ROOT + "/get-only")
        Map<String, Boolean> getOnly() { return Map.of("ok", true); }

        @PostMapping(path = ROOT + "/json-body", consumes = MediaType.APPLICATION_JSON_VALUE)
        Map<String, Object> body(@RequestBody Map<String, Object> body) { return body; }

        @GetMapping(ROOT + "/api-exception/{status}")
        void apiException(@PathVariable int status) {
            throw ApiException.of(REQUEST_ID, status, "EXISTING_PUBLIC_ERROR", "Existing safe public message", status >= 500);
        }

        @GetMapping(ROOT + "/unexpected")
        void unexpected() { throw new IllegalStateException(PRIVATE_DETAIL); }

        @GetMapping(ROOT + "/http-exception/{status}")
        void httpException(@PathVariable int status) {
            throw responseStatusException(status);
        }

        private ResponseStatusException responseStatusException(int status) {
            var exception = new ResponseStatusException(HttpStatusCode.valueOf(status), PRIVATE_DETAIL) {
                @Override public HttpHeaders getHeaders() {
                    HttpHeaders headers = new HttpHeaders();
                    if (status == 429) {
                        headers.set(HttpHeaders.RETRY_AFTER, "120");
                        headers.setContentType(MediaType.APPLICATION_PROBLEM_JSON);
                        headers.setContentLength(9999);
                    }
                    return headers;
                }
            };
            exception.getBody().setTitle(PRIVATE_DETAIL);
            exception.getBody().setProperty("internal_context", PRIVATE_DETAIL);
            return exception;
        }

        @GetMapping(ROOT + "/generic-http-exception")
        void genericHttpException() {
            var body = ProblemDetail.forStatusAndDetail(HttpStatusCode.valueOf(422), PRIVATE_DETAIL);
            body.setTitle(PRIVATE_DETAIL);
            body.setProperty("internal_context", PRIVATE_DETAIL);
            var exception = new ErrorResponseException(HttpStatusCode.valueOf(422), body,
                    new IllegalArgumentException(PRIVATE_DETAIL));
            throw exception;
        }

        @GetMapping(ROOT + "/wrapped-http-exception")
        void wrappedHttpException() { throw new IllegalStateException(PRIVATE_DETAIL, responseStatusException(429)); }

        @GetMapping(ROOT + "/annotated-value")
        void annotatedValue() { throw new GoneValueException(); }

        @GetMapping(ROOT + "/annotated-code")
        void annotatedCode() { throw new GoneCodeException(); }

        @GetMapping(ROOT + "/wrapped-annotated-value")
        void wrappedAnnotatedValue() { throw new IllegalStateException(PRIVATE_DETAIL, new GoneValueException()); }

        @GetMapping(ROOT + "/wrapped-annotated-code")
        void wrappedAnnotatedCode() { throw new IllegalStateException(PRIVATE_DETAIL, new GoneCodeException()); }
    }

    @ResponseStatus(value = HttpStatus.GONE, reason = PRIVATE_DETAIL)
    static class GoneValueException extends RuntimeException {
        GoneValueException() { super(PRIVATE_DETAIL); }
    }

    @ResponseStatus(code = HttpStatus.GONE, reason = PRIVATE_DETAIL)
    static class GoneCodeException extends RuntimeException {
        GoneCodeException() { super(PRIVATE_DETAIL); }
    }
}
