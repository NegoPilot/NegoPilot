package com.negopilot.backend.negotiation;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.agent.GenerationGateway;
import com.negopilot.backend.agent.LegalReviewRequestFactory;
import com.negopilot.backend.agent.MailGateway;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.supplier.SupplierLookupGateway;
import java.net.URI;
import java.nio.file.Path;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.UUID;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;
import static com.negopilot.backend.negotiation.NegotiationFixtures.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** 메일 미리보기·발송·복구를 실제 PostgreSQL(V1~V4)과 mail 게이트웨이 대역으로 검증한다. */
@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
class NegotiationMailPostgresTest {
    static final String HASH = "sha256:" + "b".repeat(64);
    static JdbcTemplate admin;
    static JdbcTemplate jdbc;
    static DriverManagerDataSource datasource;
    static String schema;
    @TempDir Path directory;
    UUID owner;
    MailGateway mail;
    NegotiationService service;
    NegotiationMailService mails;

    @BeforeAll static void migrate() {
        String base = System.getenv("TEST_DATABASE_URL");
        if (!base.matches("jdbc:postgresql://[^/]+/[A-Za-z0-9_]*test(?:\\?.*)?"))
            throw new IllegalStateException("명시적으로 분리된 test DB만 사용할 수 있습니다.");
        String user = System.getenv("TEST_DATABASE_USER");
        String password = System.getenv().getOrDefault("TEST_DATABASE_PASSWORD", "");
        admin = new JdbcTemplate(new DriverManagerDataSource(base, user, password));
        schema = "negotiation_mail_" + UUID.randomUUID().toString().replace("-", "");
        String url = base + (base.contains("?") ? "&" : "?") + "currentSchema=" + schema;
        datasource = new DriverManagerDataSource(url, user, password);
        Flyway.configure().dataSource(datasource).schemas(schema).defaultSchema(schema)
                .locations("classpath:db/migration").load().migrate();
        jdbc = new JdbcTemplate(datasource);
    }

    @AfterAll static void cleanup() {
        if (admin != null && schema != null) admin.execute("DROP SCHEMA IF EXISTS " + schema + " CASCADE");
    }

    @BeforeEach void setup() {
        owner = UUID.randomUUID();
        jdbc.update("INSERT INTO app_users(id,email,name,department,job_title,password_hash,role,status) VALUES (?,?,?,'구매팀','매니저','unused','USER','ACTIVE')",
                owner, owner + "@example.com", "담당자");
        var repository = new NegotiationRepository(jdbc, JSON);
        var transaction = new TransactionTemplate(new DataSourceTransactionManager(datasource));
        var properties = new AgentProperties(URI.create("http://agent.invalid"), "test", Duration.ofSeconds(1), Duration.ofSeconds(2));
        GenerationGateway gateway = mock(GenerationGateway.class);
        SupplierLookupGateway suppliers = mock(SupplierLookupGateway.class);
        when(suppliers.detail(anyString(), any())).thenReturn(object("{\"data\":{\"supplier_id\":\"SUP-0060\",\"name\":\"온새프레스\"}}"));
        when(gateway.review(any(), any())).thenAnswer(call -> reviewResponse(call.getArgument(0), "ready"));
        when(gateway.propose(any(), any())).thenAnswer(call -> proposalResponse(call.getArgument(0)));
        when(gateway.render(any(), any(), anyString())).thenAnswer(call -> renderResponse(call.getArgument(0), call.getArgument(1), call.getArgument(2)));
        var requests = new NegotiationRequests(repository, transaction, JSON, properties);
        var store = new LocalNegotiationExportStore(directory);
        service = new NegotiationService(repository, transaction, gateway, suppliers, new LegalReviewRequestFactory(JSON), JSON, requests, store, properties);
        mail = mock(MailGateway.class);
        when(mail.preview(any(), any(), any(), any(), any())).thenAnswer(call -> {
            assertThat(TransactionSynchronizationManager.isActualTransactionActive()).isFalse();
            assertThat(call.<ObjectNode>getArgument(1).path("status").asText()).isEqualTo("final");
            assertThat(call.<JsonNode>getArgument(2)).hasSize(2);
            return preview();
        });
        mails = new NegotiationMailService(repository, transaction, mail, JSON, requests, store, properties);
    }

    @Test void previewKeepsTokenServerSideAndSendUsesSameInputOnce() {
        Flow flow = exported(true);
        JsonNode preview = mails.preview(owner, flow.workspace, flow.proposal, previewBody(flow.bundle, null), null, UUID.randomUUID()).body();
        assertThat(preview.toString()).doesNotContain("confirmation_token", "tok-1");
        assertThat(preview.path("status").asText()).isEqualTo("PREVIEWED");
        UUID mailId = UUID.fromString(preview.path("mail_id").asText());
        assertThat(jdbc.queryForObject("SELECT confirmation_token FROM neg_mail_sends WHERE id=?", String.class, mailId)).isEqualTo("tok-1");
        when(mail.send(any(), any(), any(), anyLong(), any(), anyString(), anyString(), anyString()))
                .thenReturn(object("{\"send_id\":\"" + sendId("s-1") + "\",\"status\":\"SENT\",\"message_id\":\"<m@x>\"}"));
        UUID key = UUID.randomUUID();
        var sent = mails.send(owner, mailId, sendBody(), key, UUID.randomUUID());
        var again = mails.send(owner, mailId, sendBody(), key, UUID.randomUUID());
        assertThat(sent.status()).isEqualTo(200);
        assertThat(sent.body().path("status").asText()).isEqualTo("SENT");
        assertThat(again.body()).isEqualTo(sent.body());
        verify(mail, times(1)).send(any(), any(), any(), eq(7L), isNull(), eq("tok-1"), eq(owner.toString()), eq(mailId.toString()));
        assertThat(jdbc.queryForObject("SELECT confirmation_token FROM neg_mail_sends WHERE id=?", String.class, mailId)).isNull();
        assertThat(code(() -> mails.send(owner, mailId, sendBody(), UUID.randomUUID(), UUID.randomUUID()))).isEqualTo("ALREADY_SENT");
    }

    @Test void secondPreviewCannotSendTheSameVersionToTheSameContactAgain() {
        Flow flow = exported(true);
        UUID first = previewed(flow, null); UUID second = previewed(flow, null);
        when(mail.send(any(), any(), any(), anyLong(), any(), anyString(), anyString(), anyString()))
                .thenReturn(object("{\"send_id\":\"" + sendId("s-1") + "\",\"status\":\"SENT\",\"message_id\":null}"));
        mails.send(owner, first, sendBody(), UUID.randomUUID(), UUID.randomUUID());
        assertThat(code(() -> mails.send(owner, second, sendBody(), UUID.randomUUID(), UUID.randomUUID()))).isEqualTo("ALREADY_SENT");
        verify(mail, times(1)).send(any(), any(), any(), anyLong(), any(), anyString(), anyString(), anyString());
        assertThat(jdbc.queryForObject("SELECT status FROM neg_mail_sends WHERE id=?", String.class, second)).isEqualTo("PREVIEWED");
    }

    @Test void termsChangedAfterPreviewMakeThePreviewStaleWithoutCallingMail() {
        Flow flow = exported(true);
        UUID mailId = previewed(flow, null);
        service.patchProposal(owner, flow.workspace, flow.proposal, object("{\"base_revision\":1,\"changes\":{\"offer\":{\"payment_days\":45}}}"), null, UUID.randomUUID());
        service.validate(owner, flow.workspace, flow.proposal, 2, object("{}"), null, UUID.randomUUID());
        assertThat(code(() -> mails.send(owner, mailId, sendBody(), UUID.randomUUID(), UUID.randomUUID()))).isEqualTo("PREVIEW_STALE");
        assertThat(jdbc.queryForObject("SELECT status FROM neg_mail_sends WHERE id=?", String.class, mailId)).isEqualTo("STALE");
        assertThat(jdbc.queryForObject("SELECT confirmation_token FROM neg_mail_sends WHERE id=?", String.class, mailId)).isNull();
        verify(mail, never()).send(any(), any(), any(), anyLong(), any(), anyString(), anyString(), anyString());
    }

    @Test void internalOnlyEditAfterPreviewStillSendsTheSameDocument() {
        Flow flow = exported(true);
        UUID mailId = previewed(flow, null);
        service.patchProposal(owner, flow.workspace, flow.proposal, object("{\"base_revision\":1,\"changes\":{\"writing\":{\"strategy\":\"s2\"}}}"), null, UUID.randomUUID());
        when(mail.send(any(), any(), any(), anyLong(), any(), anyString(), anyString(), anyString()))
                .thenReturn(object("{\"send_id\":\"" + sendId("s-2") + "\",\"status\":\"DRY_RUN\",\"message_id\":null}"));
        assertThat(mails.send(owner, mailId, sendBody(), UUID.randomUUID(), UUID.randomUUID()).body().path("status").asText()).isEqualTo("DRY_RUN");
    }

    @Test void draftExportOrInternalPriceInMessageIsRejectedBeforeMail() {
        Flow draft = exported(false);
        assertThat(code(() -> mails.preview(owner, draft.workspace, draft.proposal, previewBody(draft.bundle, null), null, UUID.randomUUID())))
                .isEqualTo("SEND_NOT_ALLOWED");
        Flow flow = exported(true);
        assertThat(code(() -> mails.preview(owner, flow.workspace, flow.proposal, previewBody(flow.bundle, "987,654,321원까지 검토 가능합니다"), null, UUID.randomUUID())))
                .isEqualTo("MESSAGE_INTERNAL_INFO");
        assertThat(code(() -> mails.preview(owner, flow.workspace, flow.proposal, previewBody(flow.bundle, "대외비 자료 참고"), null, UUID.randomUUID())))
                .isEqualTo("MESSAGE_INTERNAL_INFO");
        verify(mail, never()).preview(any(), any(), any(), any(), any());
        assertThat(previewed(flow, "1,948원 단가로 제안드립니다")).isNotNull();
    }

    @Test void unclearResultStaysUnknownAndReconcileLinksTheRecordByClientRef() {
        Flow flow = exported(true);
        UUID mailId = previewed(flow, null);
        when(mail.send(any(), any(), any(), anyLong(), any(), anyString(), anyString(), anyString())).thenThrow(ApiException.timeout(UUID.randomUUID()));
        var unknown = mails.send(owner, mailId, sendBody(), UUID.randomUUID(), UUID.randomUUID());
        assertThat(unknown.status()).isEqualTo(202);
        assertThat(unknown.body().path("status").asText()).isEqualTo("UNKNOWN");
        assertThat(jdbc.queryForObject("SELECT confirmation_token FROM neg_mail_sends WHERE id=?", String.class, mailId)).isEqualTo("tok-1");
        when(mail.history(any(), anyString(), eq(mailId.toString())))
                .thenReturn(JSON.readTree("[{\"send_id\":\"" + sendId("s-9") + "\",\"status\":\"SENT\",\"message_id\":\"<m@x>\",\"client_ref\":\"" + mailId + "\"}]"));
        var reconciled = mails.reconcile(owner, mailId, UUID.randomUUID());
        assertThat(reconciled.body().path("status").asText()).isEqualTo("SENT");
        assertThat(reconciled.body().path("send_id").asText()).isEqualTo(sendId("s-9"));
        verify(mail, times(1)).send(any(), any(), any(), anyLong(), any(), anyString(), anyString(), anyString());
    }

    @Test void reconcileWithoutRecordRetriesWithTheSameTokenAndClientRef() {
        Flow flow = exported(true);
        UUID mailId = previewed(flow, null);
        when(mail.send(any(), any(), any(), anyLong(), any(), anyString(), anyString(), anyString()))
                .thenThrow(ApiException.unavailable(UUID.randomUUID()))
                .thenReturn(object("{\"send_id\":\"" + sendId("s-3") + "\",\"status\":\"SENT\",\"message_id\":null}"));
        mails.send(owner, mailId, sendBody(), UUID.randomUUID(), UUID.randomUUID());
        when(mail.history(any(), anyString(), anyString())).thenReturn(JSON.readTree("[]"));
        assertThat(mails.reconcile(owner, mailId, UUID.randomUUID()).body().path("status").asText()).isEqualTo("SENT");
        verify(mail, times(2)).send(any(), any(), any(), eq(7L), isNull(), eq("tok-1"), eq(owner.toString()), eq(mailId.toString()));
    }

    @Test void rejectedByMailServerIsFailedAndNeedsANewPreview() {
        Flow flow = exported(true);
        UUID mailId = previewed(flow, null);
        when(mail.send(any(), any(), any(), anyLong(), any(), anyString(), anyString(), anyString()))
                .thenThrow(ApiException.of(UUID.randomUUID(), 422, "RECIPIENT_MISSING", "담당자 없음", false));
        var failed = mails.send(owner, mailId, sendBody(), UUID.randomUUID(), UUID.randomUUID());
        assertThat(failed.body().path("status").asText()).isEqualTo("FAILED");
        assertThat(failed.body().path("error_code").asText()).isEqualTo("RECIPIENT_MISSING");
        assertThat(code(() -> mails.send(owner, mailId, sendBody(), UUID.randomUUID(), UUID.randomUUID()))).isEqualTo("PREVIEW_USED");
        assertThat(mails.list(owner, flow.workspace, flow.proposal, UUID.randomUUID()).path("items")).hasSize(1);
    }

    @Test void sendRequiresIdempotencyKeyAndOtherOwnersCannotSeeTheMail() {
        Flow flow = exported(true);
        UUID mailId = previewed(flow, null);
        assertThat(code(() -> mails.send(owner, mailId, sendBody(), null, UUID.randomUUID()))).isEqualTo("IDEMPOTENCY_KEY_REQUIRED");
        assertThat(code(() -> mails.get(UUID.randomUUID(), mailId, UUID.randomUUID()))).isEqualTo("RESOURCE_NOT_FOUND");
    }

    private record Flow(UUID workspace, UUID proposal, UUID bundle) {}
    private Flow exported(boolean finalDocument) {
        UUID workspace = id(service.create(owner, object("{\"supplier_id\":\"SUP-0060\",\"rfq_id\":\"RFQ-2026-002\"}"), null, UUID.randomUUID()).body(), "negotiation_id");
        service.review(owner, workspace, reviewInput(), null, UUID.randomUUID());
        UUID proposal = id(service.propose(owner, workspace, object("{\"review_revision\":1,\"offer\":{}}"), null, UUID.randomUUID()).body(), "proposal_id");
        JsonNode bundle = service.export(owner, workspace, proposal, object("{\"revision\":1,\"document_type\":\"external\",\"formats\":[\"pdf\",\"docx\"],\"final\":"
                + finalDocument + "}"), null, UUID.randomUUID()).body();
        return new Flow(workspace, proposal, id(bundle, "bundle_id"));
    }
    private UUID previewed(Flow flow, String message) {
        return id(mails.preview(owner, flow.workspace, flow.proposal, previewBody(flow.bundle, message), null, UUID.randomUUID()).body(), "mail_id");
    }
    private static ObjectNode previewBody(UUID bundle, String message) {
        ObjectNode body = JSON.createObjectNode().put("bundle_id", bundle.toString());
        if (message != null) body.put("message", message);
        return body;
    }
    private static ObjectNode sendBody() { return JSON.createObjectNode().put("content_hash", HASH); }
    private static ObjectNode preview() {
        ObjectNode result = object("""
                {"to":{"contact_id":7,"name":"김영업","email":"sales@example.test","supplier_id":"SUP-0060","supplier_name":"온새프레스"},
                 "from":"NegoPilot 구매팀 <purchasing@negopilot.local>","reply_to":null,
                 "subject":"[NegoPilot] 단가 및 거래조건 제안서","body":"안녕하세요",
                 "attachments":[{"filename":"제안서.pdf","content_type":"application/pdf","bytes":10,"sha256":"x"}],
                 "dry_run":false,"confirmation_token":"tok-1"}
                """);
        return result.put("content_hash", HASH).put("expires_at", OffsetDateTime.now(ZoneOffset.UTC).plusMinutes(15).toString());
    }
    // send_id는 mail-mcp 전체에서 고유하다. 테스트끼리 같은 스키마를 쓰므로 소유자마다 다르게 만든다.
    private String sendId(String name) { return name + "-" + owner; }
    private static String code(Runnable call) {
        try { call.run(); return "OK"; } catch (ApiException failure) { return failure.code; }
    }
    private static UUID id(JsonNode row, String key) { return UUID.fromString(row.path(key).asText()); }
}
