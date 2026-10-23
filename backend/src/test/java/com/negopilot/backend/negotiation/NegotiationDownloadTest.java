package com.negopilot.backend.negotiation;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.common.api.ApiException;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.node.ObjectNode;

import static com.negopilot.backend.negotiation.NegotiationPureFixtures.JSON;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class NegotiationDownloadTest {
    private static final UUID OWNER = UUID.randomUUID();
    private static final UUID FILE = UUID.randomUUID();
    private static final UUID REQUEST = UUID.randomUUID();
    private static final String NAME = "제안서_RFQ-2026-001_SUP-0001_v1.pdf";
    private static final String KEY = "exports/2026/10/" + FILE + ".pdf";
    private static final String ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

    @Test
    void downloadsMatchingBytesAndPreservesAgentFilenameAfterOwnerJoin() {
        var repository = new FileRepository(fileRow());
        var store = new FileStore("abc");

        var result = service(repository, store).download(OWNER, FILE, REQUEST);

        assertThat(result.bytes()).isEqualTo("abc".getBytes(StandardCharsets.UTF_8));
        assertThat(result.contentType()).isEqualTo("application/pdf");
        assertThat(result.filename()).isEqualTo(NAME);
        assertThat(store.reads).isEqualTo(1);
        assertThat(repository.sql).contains("JOIN neg_export_bundles", "JOIN neg_proposals", "JOIN neg_workspaces",
                "w.owner_id=?", "b.status='ready'");
        assertThat(repository.arguments).containsExactly(FILE, OWNER);
    }

    @Test
    void corruptedStoredBytesReturnInternalErrorInsteadOfDownload() {
        var store = new FileStore("changed bytes");

        assertThatThrownBy(() -> service(new FileRepository(fileRow()), store).download(OWNER, FILE, REQUEST))
                .isInstanceOfSatisfying(ApiException.class, error -> {
                    assertThat(error.status.value()).isEqualTo(500);
                    assertThat(error.code).isEqualTo("INTERNAL_ERROR");
                    assertThat(error.requestId).isEqualTo(REQUEST);
                });
        assertThat(store.reads).isEqualTo(1);
    }

    @Test
    void missingOrForeignFileReturnsNotFoundWithoutReadingStorage() {
        var store = new FileStore("abc");

        assertThatThrownBy(() -> service(new FileRepository(null), store).download(OWNER, FILE, REQUEST))
                .isInstanceOfSatisfying(ApiException.class, error -> {
                    assertThat(error.status.value()).isEqualTo(404);
                    assertThat(error.code).isEqualTo("RESOURCE_NOT_FOUND");
                });
        assertThat(store.reads).isZero();
    }

    @Test
    void dispositionHasAsciiFallbackAndExactUtf8KoreanFilename() {
        String header = NegotiationController.disposition(NAME);
        assertThat(header).startsWith("attachment; filename=\"____RFQ-2026-001_SUP-0001_v1.pdf\"; filename*=UTF-8''");
        String encoded = header.substring(header.indexOf("UTF-8''") + 7);
        assertThat(encoded).startsWith("%EC%A0%9C%EC%95%88%EC%84%9C_");
        assertThat(URLDecoder.decode(encoded, StandardCharsets.UTF_8)).isEqualTo(NAME);
        assertThat(header).doesNotContain("제안서", "\r", "\n");

        String escaped = NegotiationController.disposition("제안서 \"최종\"\r\n.pdf");
        assertThat(escaped).contains("%20%22", "%22%0D%0A.pdf").doesNotContain("\r", "\n");
    }

    private static ObjectNode fileRow() {
        return JSON.createObjectNode().put("storage_key", KEY).put("file_name", NAME)
                .put("content_type", "application/pdf").put("sha256", ABC_SHA256);
    }

    private static NegotiationService service(FileRepository repository, FileStore store) {
        AgentProperties properties = new AgentProperties(URI.create("http://localhost:8000"), "",
                Duration.ofSeconds(1), Duration.ofSeconds(1));
        return new NegotiationService(repository, null, null, null, null, JSON, null, store, properties);
    }

    private static final class FileRepository extends NegotiationRepository {
        private final ObjectNode file;
        private String sql;
        private Object[] arguments;

        FileRepository(ObjectNode file) { super(null, JSON); this.file = file; }

        @Override
        ObjectNode one(String sql, Object... args) {
            this.sql = sql;
            this.arguments = args;
            return file;
        }
    }

    private static final class FileStore implements NegotiationExportStore {
        private final byte[] bytes;
        private int reads;

        FileStore(String contents) { bytes = contents.getBytes(StandardCharsets.UTF_8); }

        @Override
        public void write(String key, byte[] bytes) { throw new AssertionError("다운로드는 파일을 쓰지 않습니다"); }

        @Override
        public byte[] read(String key) {
            assertThat(key).isEqualTo(KEY);
            reads++;
            return bytes;
        }
    }
}
