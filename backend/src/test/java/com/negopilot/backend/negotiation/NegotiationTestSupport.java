package com.negopilot.backend.negotiation;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.agent.GenerationGateway;
import com.negopilot.backend.agent.LegalReviewRequestFactory;
import com.negopilot.backend.supplier.SupplierLookupGateway;
import java.nio.file.Path;
import java.util.UUID;
import javax.sql.DataSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import static com.negopilot.backend.negotiation.NegotiationFixtures.*;

/** 다른 패키지의 PostgreSQL 테스트가 실제 협상 작업 서비스와 NegotiationReviews를 함께 쓰게 한다. */
public final class NegotiationTestSupport {
    public final NegotiationReviews reviews;
    private final NegotiationService service;

    public NegotiationTestSupport(JdbcTemplate jdbc, DataSource datasource, GenerationGateway gateway,
                                  SupplierLookupGateway suppliers, AgentProperties properties, Path exportDirectory) {
        var repository = new NegotiationRepository(jdbc, JSON);
        var transaction = new TransactionTemplate(new DataSourceTransactionManager(datasource));
        service = new NegotiationService(repository, transaction, gateway, suppliers, new LegalReviewRequestFactory(JSON), JSON,
                new NegotiationRequests(repository, transaction, JSON, properties), new LocalNegotiationExportStore(exportDirectory), properties);
        reviews = new NegotiationReviews(service, JSON);
    }

    public static JsonNode reviewResponseFor(UUID requestId, String assessment) { return reviewResponse(requestId, assessment); }

    public static JsonNode proposalResponseFor(UUID requestId) { return proposalResponse(requestId); }

    /** 공개 API POST /api/negotiations/{id}/proposals와 같은 서비스 호출. 응답 상태 코드를 돌려준다. */
    public int propose(UUID owner, UUID workspace, int reviewRevision) {
        return service.propose(owner, workspace, object("{\"review_revision\":" + reviewRevision + ",\"offer\":{}}"), null, UUID.randomUUID()).status();
    }
}
