package com.negopilot.backend.legacy;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.agent.GenerationGateway;
import com.negopilot.backend.agent.GenerationRequestValidator;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Consumer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class GenerationConversationServiceTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final GenerationGateway gateway = mock(GenerationGateway.class);
    private final GenerationConversationService service = new GenerationConversationService(gateway, JSON);

    @Test
    void createsIndependentMemorySnapshotsWithoutLookingUpSelectedSuppliers() {
        ObjectNode selected = context("NOT-IN-A-FIXTURE");
        JsonNode created = service.create(selected, UUID.randomUUID());
        String id = created.get("conversation_id").stringValue();
        assertThat(created.propertyNames()).containsExactlyInAnyOrder(
                "schema_version", "conversation_id", "context", "storage", "messages");
        assertThat(created.get("schema_version").stringValue()).isEqualTo("0.3");
        assertThat(UUID.fromString(id).toString()).isEqualTo(id);
        assertThat(created.get("storage").stringValue()).isEqualTo("memory");
        assertThat(created.get("messages").isEmpty()).isTrue();
        selected.putArray("selected_supplier_ids");
        ((ObjectNode) created.get("context")).putArray("selected_supplier_ids");
        assertThat(service.get(id, UUID.randomUUID()).get("context")).isEqualTo(context("NOT-IN-A-FIXTURE"));
        assertThat(service.create(context(null), UUID.randomUUID()).get("conversation_id").stringValue()).isNotEqualTo(id);
        verifyNoInteractions(gateway);
    }

    @ParameterizedTest
    @ValueSource(strings = {"COMPLETED", "NEEDS_INPUT"})
    void storesBothMessagesAndTheFullAnswerForSuccessfulExecutions(String status) {
        String id = create("OLD");
        UUID requestId = UUID.randomUUID();
        ObjectNode selected = context("NEW");
        List<ObjectNode> sent = new ArrayList<>();
        List<ObjectNode> upstream = new ArrayList<>();
        when(gateway.execute(any(UUID.class), any(ObjectNode.class))).thenAnswer(invocation -> {
            ObjectNode request = invocation.getArgument(1);
            sent.add(request.deepCopy());
            ObjectNode answer = response(invocation.getArgument(0), request, "원래 생성 답변 [1]", status);
            upstream.add(answer);
            return answer;
        });

        ObjectNode actual = (ObjectNode) service.message(id, input("원래 질문", selected), requestId);
        ObjectNode expected = upstream.getFirst().deepCopy();
        expected.put("conversation_id", id).put("response_id", actual.get("response_id").stringValue());
        assertThat(actual).isEqualTo(expected);
        assertThat(upstream.getFirst().has("conversation_id")).isFalse();
        assertThat(sent.getFirst().propertyNames()).containsExactlyInAnyOrder("request_id", "query", "context", "history");
        assertThat(sent.getFirst().get("request_id").stringValue()).isEqualTo(requestId.toString());
        assertThat(sent.getFirst().get("history").isEmpty()).isTrue();

        JsonNode snapshot = service.get(id, UUID.randomUUID());
        JsonNode messages = snapshot.get("messages");
        assertThat(snapshot.get("context")).isEqualTo(selected);
        assertThat(messages.size()).isEqualTo(2);
        assertThat(messages.get(0).get("role").stringValue()).isEqualTo("user");
        assertThat(messages.get(0).get("content").stringValue()).isEqualTo("원래 질문");
        assertThat(messages.get(0).get("response").isNull()).isTrue();
        assertThat(messages.get(1).get("role").stringValue()).isEqualTo("assistant");
        assertThat(messages.get(1).get("content").stringValue()).isEqualTo("원래 생성 답변 [1]");
        assertThat(messages.get(1).get("message_id")).isEqualTo(actual.get("response_id"));
        assertThat(messages.get(1).get("response")).isEqualTo(actual);
        assertThat(messages.get(0).get("message_id")).isNotEqualTo(actual.get("response_id"));

        selected.putArray("selected_supplier_ids");
        ((ObjectNode) actual.get("answer")).put("text", "응답 사본 변경");
        ((ObjectNode) snapshot.get("context")).putArray("selected_supplier_ids");
        ((ObjectNode) messages.get(1)).put("content", "스냅샷 사본 변경");
        assertThat(service.get(id, UUID.randomUUID()).get("context")).isEqualTo(context("NEW"));
        assertThat(service.get(id, UUID.randomUUID()).path("messages").get(1).path("response").path("answer").path("text").stringValue())
                .isEqualTo("원래 생성 답변 [1]");

        service.message(id, input("다음 질문", null), UUID.randomUUID());
        assertThat(sent.get(1).get("context")).isEqualTo(context("NEW"));
        assertThat(sent.get(1).get("history")).isEqualTo(JSON.readTree("""
                [{"role":"user","content":"원래 질문"},{"role":"assistant","content":"원래 생성 답변 [1]"}]
                """));
    }

    @Test
    void failedExecutionReturnsItsFullResponseWithoutSavingMessagesOrContext() {
        String id = create("ORIGINAL");
        when(gateway.execute(any(UUID.class), any(ObjectNode.class))).thenAnswer(invocation ->
                response(invocation.getArgument(0), invocation.getArgument(1), "이전 답변", "COMPLETED"));
        service.message(id, input("이전 질문", null), UUID.randomUUID());
        JsonNode before = service.get(id, UUID.randomUUID());
        doAnswer(invocation -> response(invocation.getArgument(0), invocation.getArgument(1), "생성 실패 안내", "FAILED"))
                .when(gateway).execute(any(UUID.class), any(ObjectNode.class));

        JsonNode actual = service.message(id, input("새 질문", context("UNCOMMITTED")), UUID.randomUUID());
        assertThat(actual.get("execution_status").stringValue()).isEqualTo("FAILED");
        assertThat(actual.get("context")).isEqualTo(context("UNCOMMITTED"));
        assertThat(actual.path("answer").path("text").stringValue()).isEqualTo("생성 실패 안내");
        assertThat(actual.get("conversation_id").stringValue()).isEqualTo(id);
        assertThat(UUID.fromString(actual.get("response_id").stringValue())).isNotNull();
        assertThat(service.get(id, UUID.randomUUID())).isEqualTo(before);
    }

    @ParameterizedTest
    @ValueSource(strings = {"UPSTREAM_UNAVAILABLE", "UPSTREAM_TIMEOUT", "UPSTREAM_INVALID_RESPONSE"})
    void gatewayErrorsPreserveThePreviousTurnAndContext(String code) {
        String id = create("ORIGINAL");
        when(gateway.execute(any(UUID.class), any(ObjectNode.class))).thenAnswer(invocation ->
                response(invocation.getArgument(0), invocation.getArgument(1), "기존 답변", "COMPLETED"));
        service.message(id, input("기존 질문", null), UUID.randomUUID());
        JsonNode before = service.get(id, UUID.randomUUID());
        UUID requestId = UUID.randomUUID();
        ApiException failure = switch (code) {
            case "UPSTREAM_UNAVAILABLE" -> ApiException.unavailable(requestId);
            case "UPSTREAM_TIMEOUT" -> ApiException.timeout(requestId);
            default -> ApiException.invalidResponse(requestId);
        };
        doThrow(failure).when(gateway).execute(any(UUID.class), any(ObjectNode.class));
        assertThatThrownBy(() -> service.message(id, input("저장하지 않을 질문", context(null)), requestId))
                .isSameAs(failure);
        assertThat(service.get(id, UUID.randomUUID())).isEqualTo(before);
    }

    @Test
    void cannotCommitAResponseWithoutTheTextNeededToStoreTheTurn() {
        String id = create("ORIGINAL");
        JsonNode before = service.get(id, UUID.randomUUID());
        when(gateway.execute(any(UUID.class), any(ObjectNode.class))).thenReturn(
                JSON.createObjectNode().put("execution_status", "COMPLETED"));
        assertThatThrownBy(() -> service.message(id, input("질문", context("NEW")), UUID.randomUUID()))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code).isEqualTo("UPSTREAM_INVALID_RESPONSE"));
        assertThat(service.get(id, UUID.randomUUID())).isEqualTo(before);
    }

    @Test
    void longAssistantTextIsPreservedAndOnlyClippedAtTheNextHistoryBoundary() {
        String id = create(null);
        String user = "🧑".repeat(4000);
        String answer = "😀".repeat(4000) + "추가 설명";
        List<ObjectNode> sent = new ArrayList<>();
        when(gateway.execute(any(UUID.class), any(ObjectNode.class))).thenAnswer(invocation -> {
            ObjectNode request = invocation.getArgument(1);
            sent.add(request.deepCopy());
            return response(invocation.getArgument(0), request, answer, "COMPLETED");
        });
        JsonNode actual = service.message(id, input(user, null), UUID.randomUUID());
        service.message(id, input("후속 질문", null), UUID.randomUUID());

        assertThat(actual.path("answer").path("text").stringValue()).isEqualTo(answer);
        assertThat(sent.get(1).path("history").get(0).path("content").stringValue()).isEqualTo(user);
        String clipped = sent.get(1).path("history").get(1).path("content").stringValue();
        assertThat(clipped).isEqualTo("😀".repeat(4000));
        assertThat(clipped.codePointCount(0, clipped.length())).isEqualTo(4000);
        JsonNode stored = service.get(id, UUID.randomUUID()).path("messages").get(1);
        assertThat(stored.path("content").stringValue()).isEqualTo(answer);
        assertThat(stored.path("response").path("answer").path("text").stringValue()).isEqualTo(answer);
    }

    @ParameterizedTest
    @ValueSource(strings = {" ", "\t", "\u2003", "\u00a0", "\u0085", " \t\u00a0\u0085"})
    void blankHistoryPrefixMovesToTheFirstNonblankCodePointWithoutSplittingEmoji(String whitespace) {
        String answer = whitespace.repeat(4001) + "😀".repeat(4001) + "나머지 설명";
        String forwarded = forwardAnswerAsHistory(answer);
        assertThat(forwarded).isEqualTo("😀".repeat(4000));
        assertThat(forwarded.codePointCount(0, forwarded.length())).isEqualTo(4000);
    }

    @Test
    void blankHistoryPrefixKeepsACompleteShortSuffix() {
        assertThat(forwardAnswerAsHistory(" ".repeat(4000) + "정상 답변 😀 ")).isEqualTo("정상 답변 😀 ");
    }

    @Test
    void nonblankPrefixKeepsItsOriginalWhitespaceAndLastEmoji() {
        String prefix = " \t\u00a0\u0085".repeat(999) + " \t\u0085😀";
        assertThat(prefix.codePointCount(0, prefix.length())).isEqualTo(4000);
        assertThat(forwardAnswerAsHistory(prefix + "뒤의 설명")).isEqualTo(prefix);
    }

    @Test
    void sendsEveryNumberedOptionOnTheNextTurnWithoutChangingStoredContentOrResponse() {
        String answer = "어느 기업을 확인할까요?";
        String forwarded = forwardAnswerAsHistory(answer, result -> {
            result.put("execution_status", "NEEDS_INPUT");
            ObjectNode generated = (ObjectNode) result.get("answer");
            generated.put("kind", "clarify");
            generated.putArray("options").add("해솔정밀 (SUP-0041)").add("해솔기공 (SUP-0042)");
        });
        assertThat(forwarded).isEqualTo(answer + "\n\n선택지 데이터:\n"
                + "[{\"number\":1,\"text\":\"해솔정밀 (SUP-0041)\"},{\"number\":2,\"text\":\"해솔기공 (SUP-0042)\"}]");
    }

    @Test
    void optionsRetainTheirUnicodeEmojiAndEscapedJsonCharacters() {
        String first = "해솔 \"정밀\" 😀\n서울\\부산";
        String second = "두 번째\t🧑‍🔧";
        String forwarded = forwardAnswerAsHistory("선택해 주세요.", result ->
                ((ObjectNode) result.get("answer")).putArray("options").add(first).add(second));
        String encoded = forwarded.substring(forwarded.indexOf("\n\n선택지 데이터:\n") + "\n\n선택지 데이터:\n".length());
        JsonNode decoded = JSON.readTree(encoded);
        assertThat(decoded.size()).isEqualTo(2);
        assertThat(decoded.get(0).get("number").intValue()).isEqualTo(1);
        assertThat(decoded.get(0).get("text").stringValue()).isEqualTo(first);
        assertThat(decoded.get(1).get("number").intValue()).isEqualTo(2);
        assertThat(decoded.get(1).get("text").stringValue()).isEqualTo(second);
        assertThat(encoded).contains("\\\"정밀\\\"", "\\n", "서울\\\\부산", "\\t");
    }

    @Test
    void reservesTheCompleteOptionsBeforeClippingALongEmojiAnswer() {
        String suffix = "\n\n선택지 데이터:\n[{\"number\":1,\"text\":\"선택 😀\"},{\"number\":2,\"text\":\"다른 선택\"}]";
        int bodyLimit = 4000 - suffix.codePointCount(0, suffix.length());
        String forwarded = forwardAnswerAsHistory("😀".repeat(5000) + "원문 끝", result ->
                ((ObjectNode) result.get("answer")).putArray("options").add("선택 😀").add("다른 선택"));
        assertThat(forwarded).isEqualTo("😀".repeat(bodyLimit) + suffix);
        assertThat(forwarded.codePointCount(0, forwarded.length())).isEqualTo(4000);
    }

    @Test
    void retainsOneFullEmojiWhenTheOptionsLeaveExactlyOneBodyCodePoint() {
        String emptySuffix = "\n\n선택지 데이터:\n[{\"number\":1,\"text\":\"\"}]";
        String option = "가".repeat(3999 - emptySuffix.codePointCount(0, emptySuffix.length()));
        String forwarded = forwardAnswerAsHistory("😀본문 전체", result ->
                ((ObjectNode) result.get("answer")).putArray("options").add(option));
        assertThat(forwarded).isEqualTo("😀\n\n선택지 데이터:\n[{\"number\":1,\"text\":\"" + option + "\"}]");
        assertThat(forwarded.codePointCount(0, forwarded.length())).isEqualTo(4000);
    }

    @ParameterizedTest
    @ValueSource(ints = {4000, 4001})
    void omitsAllOptionsWhenTheirEncodedSuffixLeavesNoBodySpace(int suffixLength) {
        String emptySuffix = "\n\n선택지 데이터:\n[{\"number\":1,\"text\":\"\"},{\"number\":2,\"text\":\"유지하면 안 되는 일부 선택지\"}]";
        String option = "가".repeat(suffixLength - emptySuffix.codePointCount(0, emptySuffix.length()));
        String forwarded = forwardAnswerAsHistory("😀".repeat(4001), result ->
                ((ObjectNode) result.get("answer")).putArray("options").add(option).add("유지하면 안 되는 일부 선택지"));
        assertThat(forwarded).isEqualTo("😀".repeat(4000));
    }

    @Test
    void measuresEscapedOptionsInsteadOfTheirUnencodedLengthWhenReservingSpace() {
        String option = "\n".repeat(2500);
        String forwarded = forwardAnswerAsHistory("😀".repeat(4001), result ->
                ((ObjectNode) result.get("answer")).putArray("options").add(option));
        assertThat(forwarded).isEqualTo("😀".repeat(4000));
    }

    @Test
    void reservedOptionsDoNotMakeTheClippedBodyBlank() {
        String suffix = "\n\n선택지 데이터:\n[{\"number\":1,\"text\":\"기업 😀\"}]";
        int bodyLimit = 4000 - suffix.codePointCount(0, suffix.length());
        String answer = " \t\u00a0\u0085".repeat(1001) + "😀".repeat(4001);
        String forwarded = forwardAnswerAsHistory(answer, result ->
                ((ObjectNode) result.get("answer")).putArray("options").add("기업 😀"));
        assertThat(forwarded).isEqualTo("😀".repeat(bodyLimit) + suffix);
        assertThat(forwarded.codePointCount(0, forwarded.length())).isEqualTo(4000);
    }

    @Test
    void missingOptionsKeepTheExistingHistoryClipping() {
        String forwarded = forwardAnswerAsHistory("😀".repeat(4001), result ->
                ((ObjectNode) result.get("answer")).remove("options"));
        assertThat(forwarded).isEqualTo("😀".repeat(4000));
    }

    @Test
    void sendsOnlyTheLatestTwentyMessagesAndRetainsOneHundredInCompletePairs() {
        String id = create(null);
        List<ObjectNode> sent = new ArrayList<>();
        when(gateway.execute(any(UUID.class), any(ObjectNode.class))).thenAnswer(invocation -> {
            ObjectNode request = invocation.getArgument(1);
            sent.add(request.deepCopy());
            return response(invocation.getArgument(0), request, "answer-" + request.get("query").stringValue(), "COMPLETED");
        });
        for (int index = 0; index < 51; index++) {
            service.message(id, input("query-" + index, null), UUID.randomUUID());
        }

        JsonNode lastHistory = sent.getLast().get("history");
        assertThat(lastHistory.size()).isEqualTo(20);
        assertThat(lastHistory.get(0).get("content").stringValue()).isEqualTo("query-40");
        assertThat(lastHistory.get(19).get("content").stringValue()).isEqualTo("answer-query-49");
        JsonNode saved = service.get(id, UUID.randomUUID()).get("messages");
        assertThat(saved.size()).isEqualTo(100);
        for (int index = 0; index < saved.size(); index += 2) {
            assertThat(saved.get(index).get("role").stringValue()).isEqualTo("user");
            assertThat(saved.get(index + 1).get("role").stringValue()).isEqualTo("assistant");
            assertThat(saved.get(index + 1).get("response").get("response_id"))
                    .isEqualTo(saved.get(index + 1).get("message_id"));
        }
        assertThat(saved.get(0).get("content").stringValue()).isEqualTo("query-1");
        assertThat(saved.get(99).get("content").stringValue()).isEqualTo("answer-query-50");
    }

    @Test
    void serializesOneConversationWhileOtherConversationsContinue() throws Exception {
        String firstConversation = create("OLD");
        String otherConversation = create("OTHER");
        CountDownLatch firstEntered = new CountDownLatch(1);
        CountDownLatch releaseFirst = new CountDownLatch(1);
        CountDownLatch secondStarted = new CountDownLatch(1);
        Map<String, ObjectNode> requests = new ConcurrentHashMap<>();
        when(gateway.execute(any(UUID.class), any(ObjectNode.class))).thenAnswer(invocation -> {
            ObjectNode request = invocation.getArgument(1);
            String query = request.get("query").stringValue();
            requests.put(query, request.deepCopy());
            if (query.equals("first")) {
                firstEntered.countDown();
                if (!releaseFirst.await(10, TimeUnit.SECONDS)) throw new AssertionError("First request was not released");
            }
            return response(invocation.getArgument(0), request, "answer-" + query, "COMPLETED");
        });

        try (var executor = Executors.newFixedThreadPool(3)) {
            try {
                var first = executor.submit(() -> service.message(firstConversation, input("first", context("NEW")), UUID.randomUUID()));
                assertThat(firstEntered.await(3, TimeUnit.SECONDS)).isTrue();
                var second = executor.submit(() -> {
                    secondStarted.countDown();
                    return service.message(firstConversation, input("second", null), UUID.randomUUID());
                });
                assertThat(secondStarted.await(3, TimeUnit.SECONDS)).isTrue();
                var independent = executor.submit(() -> service.message(otherConversation, input("independent", null), UUID.randomUUID()));
                assertThat(independent.get(3, TimeUnit.SECONDS).path("answer").path("text").stringValue())
                        .isEqualTo("answer-independent");
                assertThatThrownBy(() -> second.get(150, TimeUnit.MILLISECONDS)).isInstanceOf(TimeoutException.class);
                assertThat(requests).doesNotContainKey("second");
                releaseFirst.countDown();
                first.get(3, TimeUnit.SECONDS);
                second.get(3, TimeUnit.SECONDS);
            } finally {
                releaseFirst.countDown();
            }
        }
        assertThat(requests.get("second").get("context")).isEqualTo(context("NEW"));
        assertThat(requests.get("second").get("history")).isEqualTo(JSON.readTree("""
                [{"role":"user","content":"first"},{"role":"assistant","content":"answer-first"}]
                """));
        assertThat(service.get(firstConversation, UUID.randomUUID()).get("messages").size()).isEqualTo(4);
        assertThat(service.get(otherConversation, UUID.randomUUID()).get("messages").size()).isEqualTo(2);
    }

    @Test
    void missingConversationsDoNotCallTheGateway() {
        UUID requestId = UUID.randomUUID();
        String missing = UUID.randomUUID().toString();
        assertThatThrownBy(() -> service.get(missing, requestId)).isInstanceOfSatisfying(ApiException.class, error -> {
            assertThat(error.status.value()).isEqualTo(404);
            assertThat(error.code).isEqualTo("CONVERSATION_NOT_FOUND");
            assertThat(error.requestId).isEqualTo(requestId);
        });
        assertThatThrownBy(() -> service.message(missing, input("q", null), requestId))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code).isEqualTo("CONVERSATION_NOT_FOUND"));
        verifyNoInteractions(gateway);
    }

    private String create(String supplierId) {
        return service.create(context(supplierId), UUID.randomUUID()).get("conversation_id").stringValue();
    }

    private String forwardAnswerAsHistory(String answer) {
        return forwardAnswerAsHistory(answer, response -> {});
    }

    private String forwardAnswerAsHistory(String answer, Consumer<ObjectNode> customizeResponse) {
        String id = create(null);
        List<ObjectNode> sent = new ArrayList<>();
        when(gateway.execute(any(UUID.class), any(ObjectNode.class))).thenAnswer(invocation -> {
            ObjectNode request = invocation.getArgument(1);
            sent.add(request.deepCopy());
            ObjectNode result = response(invocation.getArgument(0), request, answer, "COMPLETED");
            customizeResponse.accept(result);
            return result;
        });
        JsonNode first = service.message(id, input("첫 질문", null), UUID.randomUUID());
        JsonNode storedBeforeNextTurn = service.get(id, UUID.randomUUID()).path("messages").get(1);
        service.message(id, input("2번", null), UUID.randomUUID());
        new GenerationRequestValidator().validateQuery(sent.get(1));
        assertThat(sent.get(1).get("query").stringValue()).isEqualTo("2번");
        JsonNode stored = service.get(id, UUID.randomUUID()).path("messages").get(1);
        assertThat(first.path("answer").path("text").stringValue()).isEqualTo(answer);
        assertThat(stored.path("content").stringValue()).isEqualTo(answer);
        assertThat(stored.path("response")).isEqualTo(first);
        assertThat(stored).isEqualTo(storedBeforeNextTurn);
        return sent.get(1).path("history").get(1).path("content").stringValue();
    }

    private static GenerationRequestValidator.MessageRequest input(String query, ObjectNode context) {
        return new GenerationRequestValidator.MessageRequest(query, context);
    }

    private static ObjectNode context(String supplierId) {
        ObjectNode result = JSON.createObjectNode();
        var ids = result.putArray("selected_supplier_ids");
        if (supplierId != null) ids.add(supplierId);
        return result;
    }

    private static ObjectNode response(UUID id, ObjectNode request, String text, String status) {
        ObjectNode result = JSON.createObjectNode().put("schema_version", "0.3").put("request_id", id.toString())
                .put("mode", "live").put("execution_status", status)
                .put("generation_status", "FAILED".equals(status) ? "FAILED" : "COMPLETED");
        result.set("context", request.get("context").deepCopy());
        ObjectNode decomposition = (ObjectNode) JSON.readTree("""
                {"schema_version":"0.1","status":"ok","original_query":"",
                 "sub_queries":[{"id":"q1","question":"법률 확인","original_text":"법률 확인","intent":"law_check",
                    "grounding":"required","confidence":0.9,"entities":{},"inputs":[],"depends_on":[]}],
                 "clarification":null,"assumptions":[]}
                """);
        decomposition.put("original_query", request.get("query").stringValue());
        result.set("decomposition", decomposition);
        result.set("routing", JSON.readTree("""
                {"schema_version":"0.1","status":"routed","outcomes":[
                   {"id":"q1","intent":"law_check","status":"ok","missing":[],"message":null,
                    "calls":[{"server":"law-mcp","tool":"get_article","args":{"article":"4"},
                       "result":{"article":"제4조","text":"근거 원문"},"error":null}],"referenced_articles":[]}],
                 "halted":false,"halt_reason":null}
                """));
        ObjectNode answer = (ObjectNode) JSON.readTree("""
                {"kind":"answer","text":"","citations":[{"n":1,"evidence_id":"E1","label":"출처","source_url":null}],
                 "options":[],"grounded":true,"notices":["부분 결과 안내"],
                 "evidence":[{"id":"E1","sub_query":"q1","source":"law","kind":"article","text":"근거 원문",
                    "citable":true,"label":"출처","source_url":null,"meta":{"value":42}}],
                 "verification":{"attempts":2,"masked":true,"unknown_citations":[],"ungrounded_numbers":["999"],"ungrounded_articles":[]}}
                """);
        answer.put("text", text).put("kind", switch (status) {
            case "FAILED" -> "failed";
            case "NEEDS_INPUT" -> "clarify";
            default -> "answer";
        });
        result.set("answer", answer);
        if ("FAILED".equals(status)) {
            result.putObject("error").put("code", "GENERATION_FAILED").put("message", "Answer generation failed.").put("retryable", false);
        } else {
            result.putNull("error");
        }
        return result;
    }
}
