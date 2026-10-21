package com.negopilot.backend;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import java.net.URI;
import java.sql.Connection;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ScriptUtils;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class PersistentConversationServiceTest {
    private final JsonMapper mapper = JsonMapper.builder().build();
    private final UUID owner = UUID.randomUUID();
    private final UUID other = UUID.randomUUID();
    private final UUID admin = UUID.randomUUID();
    private JdbcTemplate jdbc;
    private JdbcTemplate bootstrap;
    private TransactionTemplate tx;
    private String schema;
    private GenerationGateway gateway;
    private PersistentConversationService service;

    @BeforeAll void database() throws Exception {
        String url = System.getenv("TEST_DATABASE_URL");
        String user = System.getenv("TEST_DATABASE_USER");
        String password = System.getenv("TEST_DATABASE_PASSWORD");
        schema = "history_" + UUID.randomUUID().toString().replace("-", "");
        bootstrap = new JdbcTemplate(new DriverManagerDataSource(url, user, password));
        bootstrap.execute("CREATE SCHEMA " + schema);
        var source = new DriverManagerDataSource(url + (url.contains("?") ? "&" : "?") + "currentSchema=" + schema, user, password);
        jdbc = new JdbcTemplate(source);
        tx = new TransactionTemplate(new DataSourceTransactionManager(source));
        jdbc.execute("CREATE TABLE app_users(id UUID PRIMARY KEY, role TEXT NOT NULL, status TEXT NOT NULL)");
        try (Connection connection = source.getConnection()) {
            ScriptUtils.executeSqlScript(connection, new ClassPathResource("db/migration/V2__conversation_history.sql"));
        }
    }

    @AfterAll void dropSchema() { if (bootstrap != null) bootstrap.execute("DROP SCHEMA " + schema + " CASCADE"); }

    @BeforeEach void setup() {
        jdbc.execute("TRUNCATE app_conversation_requests, app_conversation_messages, app_conversations, app_users CASCADE");
        jdbc.update("INSERT INTO app_users VALUES (?, 'USER', 'ACTIVE'), (?, 'USER', 'ACTIVE'), (?, 'ADMIN', 'ACTIVE')", owner, other, admin);
        gateway = mock(GenerationGateway.class);
        reset(gateway);
        when(gateway.execute(any(), any())).thenAnswer(invocation -> answer(invocation.getArgument(0), invocation.getArgument(1), "COMPLETED", "답변"));
        service = newService();
    }

    private PersistentConversationService newService() {
        return new PersistentConversationService(jdbc, tx, gateway, mapper,
                new AgentProperties(URI.create("http://localhost:9200"), "test", Duration.ofSeconds(2), Duration.ofSeconds(35)));
    }

    private ObjectNode context(String... ids) {
        ObjectNode result = mapper.createObjectNode();
        var values = result.putArray("selected_supplier_ids");
        for (String id : ids) values.add(id);
        return result;
    }
    private UUID create() { return UUID.fromString(service.create(owner, context(), UUID.randomUUID()).get("conversation_id").stringValue()); }
    private GenerationRequestValidator.MessageRequest input(String query) { return new GenerationRequestValidator.MessageRequest(query, null); }
    private JsonNode send(UUID id, String query) { return service.message(owner, id, input(query), null, UUID.randomUUID()); }
    private ObjectNode answer(UUID requestId, ObjectNode request, String status, String text) {
        ObjectNode response = mapper.createObjectNode().put("schema_version", "0.3").put("request_id", requestId.toString())
                .put("mode", "stub").put("execution_status", status).put("generation_status", "FAILED".equals(status) ? "FAILED" : "COMPLETED");
        response.set("context", request.get("context").deepCopy());
        response.putObject("answer").put("text", text).putArray("options");
        return response;
    }
    private void code(Runnable action, String expected) {
        assertThatThrownBy(action::run).isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code).isEqualTo(expected));
    }
    private long rows(String table) { return jdbc.queryForObject("SELECT count(*) FROM " + table, Long.class); }

    @Test void persistsAcrossServiceRestartAndRetrievesStoredResponse() {
        UUID id = create();
        JsonNode response = send(id, "부산 기업을 찾아줘");
        service = newService();
        JsonNode snapshot = service.get(owner, id, UUID.randomUUID());
        assertThat(snapshot.path("schema_version").stringValue()).isEqualTo("0.4");
        assertThat(snapshot.path("storage").stringValue()).isEqualTo("database");
        assertThat(snapshot.path("title").stringValue()).isEqualTo("부산 기업을 찾아줘");
        assertThat(snapshot.path("messages").size()).isEqualTo(2);
        JsonNode stored = service.response(owner, id, UUID.fromString(response.get("response_id").stringValue()), UUID.randomUUID());
        assertThat(stored.get("query").stringValue()).isEqualTo("부산 기업을 찾아줘");
        assertThat(stored.get("response")).isEqualTo(response);
        assertThat(stored.get("response").get("schema_version").stringValue()).isEqualTo("0.3");
    }

    @Test void evenAdminsCannotReadOrMutateOtherUsersConversations() {
        UUID id = create();
        JsonNode response = send(id, "질문");
        UUID rid = UUID.fromString(response.get("response_id").stringValue());
        for (UUID actor : List.of(other, admin)) {
            code(() -> service.get(actor, id, UUID.randomUUID()), "CONVERSATION_NOT_FOUND");
            code(() -> service.rename(actor, id, "다른 제목", UUID.randomUUID()), "CONVERSATION_NOT_FOUND");
            code(() -> service.delete(actor, id, UUID.randomUUID()), "CONVERSATION_NOT_FOUND");
            code(() -> service.messages(actor, id, null, 50, UUID.randomUUID()), "CONVERSATION_NOT_FOUND");
            code(() -> service.response(actor, id, rid, UUID.randomUUID()), "CONVERSATION_NOT_FOUND");
            code(() -> service.message(actor, id, input("다른 질문"), null, UUID.randomUUID()), "CONVERSATION_NOT_FOUND");
            assertThat(service.list(actor, null, 20, UUID.randomUUID()).get("items").size()).isZero();
        }
    }

    @Test void keepsAllMessagesAndPaginatesWithoutRepeatingBoundaryRows() {
        UUID id = create();
        List<ObjectNode> requests = new ArrayList<>();
        reset(gateway);
        when(gateway.execute(any(), any())).thenAnswer(invocation -> {
            ObjectNode request = invocation.getArgument(1);
            requests.add(request.deepCopy());
            return answer(invocation.getArgument(0), request, "COMPLETED", "응답");
        });
        for (int i = 1; i <= 56; i++) send(id, "질문" + i);
        JsonNode snapshot = service.get(owner, id, UUID.randomUUID());
        assertThat(rows("app_conversation_messages")).isEqualTo(112);
        assertThat(snapshot.get("message_count").longValue()).isEqualTo(112);
        assertThat(snapshot.get("messages").size()).isEqualTo(50);
        assertThat(snapshot.get("messages").get(0).get("seq").longValue()).isEqualTo(63);
        assertThat(snapshot.get("next_cursor").stringValue()).isEqualTo("63");
        JsonNode older = service.messages(owner, id, 63L, 100, UUID.randomUUID());
        assertThat(older.get("messages").size()).isEqualTo(62);
        assertThat(older.get("messages").get(61).get("seq").longValue()).isEqualTo(62);
        assertThat(older.get("next_cursor").isNull()).isTrue();
        JsonNode history = requests.getLast().get("history");
        assertThat(history.size()).isEqualTo(20);
        assertThat(history.get(0).get("content").stringValue()).isEqualTo("질문46");
    }

    @Test void storesFullAnswersButClipsNumberedOptionsInModelHistory() {
        UUID id = create();
        String longAnswer = "😀".repeat(5000);
        AtomicInteger calls = new AtomicInteger();
        reset(gateway);
        when(gateway.execute(any(), any())).thenAnswer(invocation -> {
            ObjectNode request = invocation.getArgument(1);
            if (calls.getAndIncrement() > 0) {
                String content = request.get("history").get(1).get("content").stringValue();
                assertThat(content.codePointCount(0, content.length())).isLessThanOrEqualTo(4000);
                assertThat(content).contains("선택지 데이터:", "\"number\":1", "해솔정밀");
            }
            ObjectNode result = answer(invocation.getArgument(0), request, "NEEDS_INPUT", longAnswer);
            result.withObject("answer").withArray("options").add("해솔정밀").add("해솔산업");
            return result;
        });
        send(id, "첫 질문");
        send(id, "첫 번째");
        assertThat(service.get(owner, id, UUID.randomUUID()).get("messages").get(1).get("content").stringValue()).isEqualTo(longAnswer);
    }

    @Test void failedAndHttpErrorsDoNotCommitContextMessagesOrTitle() {
        UUID id = create();
        JsonNode before = service.get(owner, id, UUID.randomUUID());
        reset(gateway);
        when(gateway.execute(any(), any())).thenAnswer(invocation -> answer(invocation.getArgument(0), invocation.getArgument(1), "FAILED", "실패"));
        var changed = new GenerationRequestValidator.MessageRequest("실패 질문", context("SUP-0001"));
        JsonNode failed = service.message(owner, id, changed, UUID.randomUUID(), UUID.randomUUID());
        assertThat(failed.get("execution_status").stringValue()).isEqualTo("FAILED");
        assertThat(service.get(owner, id, UUID.randomUUID())).isEqualTo(before);
        reset(gateway);
        when(gateway.execute(any(), any())).thenThrow(ApiException.unavailable(UUID.randomUUID()));
        code(() -> service.message(owner, id, changed, null, UUID.randomUUID()), "UPSTREAM_UNAVAILABLE");
        assertThat(service.get(owner, id, UUID.randomUUID())).isEqualTo(before);
        assertThat(rows("app_conversation_messages")).isZero();
    }

    @Test void aDatabaseFailureRollsBackTheWholeTurnAndAllowsRetry() {
        UUID id = create();
        UUID key = UUID.randomUUID();
        jdbc.execute("CREATE FUNCTION reject_assistant() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.role='assistant' THEN RAISE EXCEPTION 'test write failure'; END IF; RETURN NEW; END $$");
        jdbc.execute("CREATE TRIGGER reject_assistant BEFORE INSERT ON app_conversation_messages FOR EACH ROW EXECUTE FUNCTION reject_assistant()");
        try {
            assertThatThrownBy(() -> service.message(owner, id, input("질문"), key, UUID.randomUUID()))
                    .isInstanceOf(org.springframework.dao.DataAccessException.class);
            assertThat(rows("app_conversation_messages")).isZero();
            assertThat(service.get(owner, id, UUID.randomUUID()).get("message_count").longValue()).isZero();
            assertThat(jdbc.queryForObject("SELECT lease_token IS NULL FROM app_conversations WHERE id=?", Boolean.class, id)).isTrue();
        } finally {
            jdbc.execute("DROP TRIGGER reject_assistant ON app_conversation_messages");
            jdbc.execute("DROP FUNCTION reject_assistant()");
        }
        service.message(owner, id, input("질문"), key, UUID.randomUUID());
        assertThat(rows("app_conversation_messages")).isEqualTo(2);
    }

    @Test void idempotencyReplaysSuccessAndRejectsAnotherBody() {
        UUID id = create();
        UUID key = UUID.randomUUID();
        JsonNode first = service.message(owner, id, input("같은 질문"), key, UUID.randomUUID());
        JsonNode second = service.message(owner, id, input("같은 질문"), key, UUID.randomUUID());
        assertThat(second).isEqualTo(first);
        verify(gateway, times(1)).execute(any(), any());
        assertThat(rows("app_conversation_messages")).isEqualTo(2);
        code(() -> service.message(owner, id, input("다른 질문"), key, UUID.randomUUID()), "IDEMPOTENCY_CONFLICT");
    }

    @Test void failedIdempotentRequestCanRetrySameBody() {
        UUID id = create();
        UUID key = UUID.randomUUID();
        reset(gateway);
        when(gateway.execute(any(), any())).thenThrow(ApiException.timeout(UUID.randomUUID()))
                .thenAnswer(invocation -> answer(invocation.getArgument(0), invocation.getArgument(1), "COMPLETED", "성공"));
        code(() -> service.message(owner, id, input("질문"), key, UUID.randomUUID()), "UPSTREAM_TIMEOUT");
        service.message(owner, id, input("질문"), key, UUID.randomUUID());
        assertThat(rows("app_conversation_messages")).isEqualTo(2);
        verify(gateway, times(2)).execute(any(), any());
    }

    @Test void concurrentMessagesAndDeleteReturnBusyWithoutHoldingTransactionDuringModelCall() throws Exception {
        UUID id = create();
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch finish = new CountDownLatch(1);
        UUID key = UUID.randomUUID();
        reset(gateway);
        when(gateway.execute(any(), any())).thenAnswer(invocation -> {
            assertThat(TransactionSynchronizationManager.isActualTransactionActive()).isFalse();
            entered.countDown();
            assertThat(finish.await(5, TimeUnit.SECONDS)).isTrue();
            return answer(invocation.getArgument(0), invocation.getArgument(1), "COMPLETED", "답변");
        });
        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            var first = executor.submit(() -> service.message(owner, id, input("질문"), key, UUID.randomUUID()));
            assertThat(entered.await(5, TimeUnit.SECONDS)).isTrue();
            code(() -> service.message(owner, id, input("질문"), key, UUID.randomUUID()), "CONVERSATION_BUSY");
            code(() -> service.message(owner, id, input("다른 질문"), null, UUID.randomUUID()), "CONVERSATION_BUSY");
            code(() -> service.delete(owner, id, UUID.randomUUID()), "CONVERSATION_BUSY");
            finish.countDown();
            first.get(5, TimeUnit.SECONDS);
        } finally { finish.countDown(); }
        assertThat(rows("app_conversation_messages")).isEqualTo(2);
    }

    @Test void disabledMemberCannotFinalizeAnInFlightAnswer() {
        UUID id = create();
        reset(gateway);
        when(gateway.execute(any(), any())).thenAnswer(invocation -> {
            jdbc.update("UPDATE app_users SET status='DISABLED' WHERE id=?", owner);
            return answer(invocation.getArgument(0), invocation.getArgument(1), "COMPLETED", "답변");
        });
        code(() -> send(id, "질문"), "ACCOUNT_NOT_ACTIVE");
        assertThat(rows("app_conversation_messages")).isZero();
        assertThat(jdbc.queryForObject("SELECT lease_token IS NULL FROM app_conversations WHERE id=?", Boolean.class, id)).isTrue();
    }

    @Test void anExpiredOrReplacedLeaseCannotCommitOrReleaseAnotherClaim() {
        UUID id = create();
        UUID newerToken = UUID.randomUUID();
        reset(gateway);
        when(gateway.execute(any(), any())).thenAnswer(invocation -> {
            jdbc.update("UPDATE app_conversations SET lease_token=?, lease_until=clock_timestamp()+interval '1 minute' WHERE id=?", newerToken, id);
            return answer(invocation.getArgument(0), invocation.getArgument(1), "COMPLETED", "늦은 답변");
        });
        code(() -> send(id, "질문"), "CONVERSATION_LEASE_EXPIRED");
        assertThat(rows("app_conversation_messages")).isZero();
        assertThat(jdbc.queryForObject("SELECT lease_token FROM app_conversations WHERE id=?", UUID.class, id)).isEqualTo(newerToken);
    }

    @Test void anUnreplacedButExpiredLeaseCannotCommit() {
        UUID id = create();
        reset(gateway);
        when(gateway.execute(any(), any())).thenAnswer(invocation -> {
            jdbc.update("UPDATE app_conversations SET lease_until=clock_timestamp()-interval '1 second' WHERE id=?", id);
            return answer(invocation.getArgument(0), invocation.getArgument(1), "COMPLETED", "늦은 답변");
        });
        code(() -> send(id, "질문"), "CONVERSATION_LEASE_EXPIRED");
        assertThat(rows("app_conversation_messages")).isZero();
        assertThat(jdbc.queryForObject("SELECT lease_token IS NULL FROM app_conversations WHERE id=?", Boolean.class, id)).isTrue();
    }

    @Test void expiredLeasesCanBeClaimedAgain() {
        UUID id = create();
        jdbc.update("UPDATE app_conversations SET lease_token=?, lease_until=clock_timestamp()-interval '1 second' WHERE id=?", UUID.randomUUID(), id);
        send(id, "다시 질문");
        assertThat(rows("app_conversation_messages")).isEqualTo(2);
    }

    @Test void answerAfterLeaseDeadlineCannotBeCommitted() {
        UUID id = create();
        reset(gateway);
        when(gateway.execute(any(), any())).thenAnswer(invocation -> {
            jdbc.update("UPDATE app_conversations SET lease_until=clock_timestamp()-interval '1 second' WHERE id=?", id);
            return answer(invocation.getArgument(0), invocation.getArgument(1), "COMPLETED", "늦은 답변");
        });
        code(() -> send(id, "질문"), "CONVERSATION_LEASE_EXPIRED");
        assertThat(rows("app_conversation_messages")).isZero();
        assertThat(jdbc.queryForObject("SELECT lease_token IS NULL FROM app_conversations WHERE id=?", Boolean.class, id)).isTrue();
    }

    @Test void persistenceFailureRollsBackBothMessagesContextTitleAndCachedResponse() {
        UUID id = create();
        JsonNode before = service.get(owner, id, UUID.randomUUID());
        UUID key = UUID.randomUUID();
        jdbc.execute("ALTER TABLE app_conversation_messages ADD CONSTRAINT reject_test_answer CHECK (role <> 'assistant' OR content <> '차단 답변')");
        try {
            reset(gateway);
            when(gateway.execute(any(), any())).thenAnswer(invocation -> answer(invocation.getArgument(0), invocation.getArgument(1), "COMPLETED", "차단 답변"));
            var input = new GenerationRequestValidator.MessageRequest("첫 질문", context("SUP-0001"));
            assertThatThrownBy(() -> service.message(owner, id, input, key, UUID.randomUUID()))
                    .isInstanceOf(org.springframework.dao.DataIntegrityViolationException.class);
            assertThat(rows("app_conversation_messages")).isZero();
            assertThat(service.get(owner, id, UUID.randomUUID())).isEqualTo(before);
            assertThat(jdbc.queryForObject("SELECT response IS NULL FROM app_conversation_requests WHERE conversation_id=? AND idempotency_key=?", Boolean.class, id, key)).isTrue();
            reset(gateway);
            when(gateway.execute(any(), any())).thenAnswer(invocation -> answer(invocation.getArgument(0), invocation.getArgument(1), "COMPLETED", "저장 성공"));
            service.message(owner, id, input, key, UUID.randomUUID());
            assertThat(rows("app_conversation_messages")).isEqualTo(2);
            assertThat(service.get(owner, id, UUID.randomUUID()).get("context")).isEqualTo(context("SUP-0001"));
        } finally {
            jdbc.execute("ALTER TABLE app_conversation_messages DROP CONSTRAINT reject_test_answer");
        }
    }

    @Test void invalidUpstreamDoesNotSavePartialHistoryAndReleasesClaim() {
        UUID id = create();
        reset(gateway);
        when(gateway.execute(any(), any())).thenAnswer(invocation -> {
            ObjectNode response = answer(invocation.getArgument(0), invocation.getArgument(1), "UNKNOWN", "잘못된 답변");
            return response;
        });
        code(() -> send(id, "질문"), "UPSTREAM_INVALID_RESPONSE");
        assertThat(rows("app_conversation_messages")).isZero();
        assertThat(jdbc.queryForObject("SELECT lease_token IS NULL FROM app_conversations WHERE id=?", Boolean.class, id)).isTrue();
    }

    @Test void nulInputIsRejectedBeforeTheModelCallOrAnyDatabaseMutation() {
        UUID id = create();
        send(id, "기존 질문");
        JsonNode before = service.get(owner, id, UUID.randomUUID());
        reset(gateway);
        code(() -> service.message(owner, id, input("새\0질문"), UUID.randomUUID(), UUID.randomUUID()), "VALIDATION_ERROR");
        code(() -> service.rename(owner, id, "새\0제목", UUID.randomUUID()), "VALIDATION_ERROR");
        code(() -> service.create(owner, context("SUP\0"), UUID.randomUUID()), "VALIDATION_ERROR");
        code(() -> service.message(owner, id, new GenerationRequestValidator.MessageRequest("새 질문", context("SUP\0")),
                UUID.randomUUID(), UUID.randomUUID()), "VALIDATION_ERROR");
        verifyNoInteractions(gateway);
        assertThat(service.get(owner, id, UUID.randomUUID())).isEqualTo(before);
        assertThat(rows("app_conversations")).isEqualTo(1);
        assertThat(rows("app_conversation_requests")).isZero();
        assertThat(jdbc.queryForObject("SELECT lease_token IS NULL FROM app_conversations WHERE id=?", Boolean.class, id)).isTrue();
    }

    @Test void nulAnywhereInUpstreamJsonPreservesHistoryAndReleasesTheLease() {
        UUID id = create();
        send(id, "기존 질문");
        JsonNode before = service.get(owner, id, UUID.randomUUID());
        List<java.util.function.Consumer<ObjectNode>> corruptions = List.of(
                response -> response.withObject("answer").put("text", "답변\0"),
                response -> response.putArray("evidence").addObject().putObject("raw").put("source", "자료\0"),
                response -> response.putObject("verification").put("field\0", "value"));
        for (var corruption : corruptions) {
            reset(gateway);
            when(gateway.execute(any(), any())).thenAnswer(invocation -> {
                ObjectNode response = answer(invocation.getArgument(0), invocation.getArgument(1), "COMPLETED", "답변");
                corruption.accept(response);
                return response;
            });
            assertThatThrownBy(() -> service.message(owner, id, input("새 질문"), UUID.randomUUID(), UUID.randomUUID()))
                    .isInstanceOfSatisfying(ApiException.class, error -> {
                        assertThat(error.status.value()).isEqualTo(502);
                        assertThat(error.code).isEqualTo("UPSTREAM_INVALID_RESPONSE");
                    });
            assertThat(service.get(owner, id, UUID.randomUUID())).isEqualTo(before);
            assertThat(rows("app_conversation_messages")).isEqualTo(2);
            assertThat(jdbc.queryForObject("SELECT count(*) FROM app_conversation_requests WHERE response IS NOT NULL", Long.class)).isZero();
            assertThat(jdbc.queryForObject("SELECT lease_token IS NULL FROM app_conversations WHERE id=?", Boolean.class, id)).isTrue();
        }
    }

    @Test void literalUnicodeEscapeTextIsValidAndPersistsWithoutConversion() {
        UUID id = create();
        String literal = "문자열 \\u0000";
        reset(gateway);
        when(gateway.execute(any(), any())).thenAnswer(invocation -> answer(invocation.getArgument(0), invocation.getArgument(1), "COMPLETED", literal));
        JsonNode result = send(id, literal);
        assertThat(result.at("/answer/text").stringValue()).isEqualTo(literal);
        JsonNode snapshot = service.get(owner, id, UUID.randomUUID());
        assertThat(snapshot.at("/messages/0/content").stringValue()).isEqualTo(literal);
        assertThat(snapshot.at("/messages/1/content").stringValue()).isEqualTo(literal);
    }

    @Test void manualTitlesArePreservedAndAutomaticTitleCountsCodePoints() {
        UUID first = create();
        service.rename(owner, first, "수동 제목", UUID.randomUUID());
        send(first, "첫 질문");
        assertThat(service.get(owner, first, UUID.randomUUID()).get("title").stringValue()).isEqualTo("수동 제목");
        UUID second = create();
        send(second, "😀".repeat(70));
        assertThat(service.get(owner, second, UUID.randomUUID()).get("title").stringValue()).isEqualTo("😀".repeat(50));
    }

    @Test void deletePermanentlyRemovesMessagesAndIdempotencyRecords() {
        UUID id = create();
        service.message(owner, id, input("질문"), UUID.randomUUID(), UUID.randomUUID());
        service.delete(owner, id, UUID.randomUUID());
        assertThat(rows("app_conversation_messages")).isZero();
        assertThat(rows("app_conversation_requests")).isZero();
        code(() -> service.get(owner, id, UUID.randomUUID()), "CONVERSATION_NOT_FOUND");
    }

    @Test void listCursorUsesStableTieBreakAndRejectsInvalidInput() {
        List<UUID> ids = List.of(create(), create(), create());
        jdbc.update("UPDATE app_conversations SET updated_at='2026-10-21T00:00:00Z'");
        JsonNode first = service.list(owner, null, 2, UUID.randomUUID());
        assertThat(first.get("items").size()).isEqualTo(2);
        JsonNode second = service.list(owner, first.get("next_cursor").stringValue(), 2, UUID.randomUUID());
        assertThat(second.get("items").size()).isEqualTo(1);
        List<String> seen = new ArrayList<>();
        first.get("items").forEach(item -> seen.add(item.get("conversation_id").stringValue()));
        second.get("items").forEach(item -> seen.add(item.get("conversation_id").stringValue()));
        assertThat(seen).containsExactlyInAnyOrderElementsOf(ids.stream().map(UUID::toString).toList());
        code(() -> service.list(owner, "broken", 20, UUID.randomUUID()), "VALIDATION_ERROR");
    }
}
