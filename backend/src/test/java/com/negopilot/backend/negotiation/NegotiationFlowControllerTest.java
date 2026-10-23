package com.negopilot.backend.negotiation;

import com.negopilot.backend.agent.AgentHttpConfiguration;
import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.agent.GenerationGateway;
import com.negopilot.backend.agent.LegalReviewRequestFactory;
import com.negopilot.backend.auth.AuthSessionService;
import com.negopilot.backend.auth.CurrentUser;
import com.negopilot.backend.auth.MembershipRepository;
import com.negopilot.backend.auth.SecurityConfiguration;
import com.negopilot.backend.supplier.SupplierLookupGateway;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.AbstractPlatformTransactionManager;
import org.springframework.transaction.support.DefaultTransactionStatus;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.context.WebApplicationContext;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import static com.negopilot.backend.negotiation.NegotiationFixtures.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@EnableWebSecurity
@EnableConfigurationProperties(AgentProperties.class)
@WebMvcTest(controllers = NegotiationController.class)
@Import({SecurityConfiguration.class, CurrentUser.class, AgentHttpConfiguration.class,
        NegotiationFlowControllerTest.FlowConfiguration.class})
class NegotiationFlowControllerTest {
    @Autowired MockMvc mvc;
    @Autowired WebApplicationContext context;
    @Autowired MemoryNegotiationRepository repository;
    @MockitoBean MembershipRepository members;
    @MockitoBean GenerationGateway gateway;
    @MockitoBean SupplierLookupGateway suppliers;
    final UUID owner = UUID.randomUUID();

    @BeforeEach void setup() {
        repository.tables.clear();
        mvc = MockMvcBuilders.webAppContextSetup(context).apply(springSecurity()).build();
        when(suppliers.detail(anyString(), any())).thenReturn(object("{\"data\":{\"supplier_id\":\"SUP-0060\",\"name\":\"온새프레스\"}}"));
        when(gateway.review(any(), any())).thenAnswer(call -> {
            assertThat(TransactionSynchronizationManager.isActualTransactionActive()).isFalse();
            return reviewResponse(call.getArgument(0), "ready");
        });
        when(gateway.propose(any(), any())).thenAnswer(call -> {
            assertThat(TransactionSynchronizationManager.isActualTransactionActive()).isFalse();
            return proposalResponse(call.getArgument(0));
        });
    }

    @Test void createReviewAndGenerateFlowKeepsTokensServerSideAndSenderFromMember() throws Exception {
        String workspace = createWorkspace();
        String review = mvc.perform(post("/api/negotiations/" + workspace + "/reviews").with(member()).with(csrf().asHeader())
                        .contentType(MediaType.APPLICATION_JSON).content(reviewInput().toString()))
                .andExpect(status().isCreated()).andExpect(jsonPath("$.review_revision").value(1))
                .andReturn().getResponse().getContentAsString();
        assertThat(review).doesNotContain("review_token", TOKEN);
        String loaded = mvc.perform(get("/api/negotiations/" + workspace + "/reviews/1").with(member()))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        assertThat(loaded).doesNotContain("review_token", TOKEN);
        assertThat(repository.table("neg_review_revisions").getFirst().get("response").toString())
                .doesNotContain("review_token", TOKEN);
        assertThat(repository.table("neg_review_revisions").getFirst().path("review_token").asText()).isEqualTo(TOKEN);

        String created = mvc.perform(post("/api/negotiations/" + workspace + "/proposals").with(member()).with(csrf().asHeader())
                        .contentType(MediaType.APPLICATION_JSON).content("{\"review_revision\":1,\"offer\":{\"target_price\":1750}}"))
                .andExpect(status().isCreated()).andExpect(jsonPath("$.proposal_revision").value(1))
                .andReturn().getResponse().getContentAsString();
        JsonNode proposal = JSON.readTree(created);
        verify(gateway).propose(any(), argThat(offer -> !offer.has("unit_price") && !offer.has("transaction")
                && offer.path("target_price").intValue() == 1750
                && offer.at("/sender/email").asText().equals("session@example.com")));
        String external = mvc.perform(get("/api/negotiations/" + workspace + "/proposals/"
                        + proposal.path("proposal_id").asText() + "/external").with(member()))
                .andExpect(status().isOk()).andExpect(jsonPath("$.external_document.version").value(1))
                .andReturn().getResponse().getContentAsString();
        assertThat(external).doesNotContain("price_range", "writing", "target_price", "max_price", Integer.toString(PRIVATE_MAX_PRICE));
    }

    @Test void senderAndOwnerSpoofingAreRejectedBeforeGatewayInvocation() throws Exception {
        String workspace = createWorkspace();
        mvc.perform(post("/api/negotiations").with(member()).with(csrf().asHeader()).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"supplier_id\":\"SUP-0060\",\"rfq_id\":\"RFQ-2026-002\",\"owner_id\":\"spoofed\"}"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error.code").value("VALIDATION_ERROR"));
        mvc.perform(post("/api/negotiations/" + workspace + "/proposals").with(member()).with(csrf().asHeader())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"review_revision\":1,\"offer\":{\"sender\":{\"email\":\"other@example.com\"}}}"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error.code").value("VALIDATION_ERROR"));
        mvc.perform(post("/api/negotiations/" + workspace + "/reviews").with(member()).with(csrf().asHeader())
                        .contentType(MediaType.APPLICATION_JSON).content(reviewInput().put("supplier", "SUP-0001").toString()))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error.code").value("VALIDATION_ERROR"));
        verifyNoInteractions(gateway);
        assertThat(repository.table("neg_workspaces")).hasSize(1);
        assertThat(repository.table("neg_review_revisions")).isEmpty();
        assertThat(repository.table("neg_proposals")).isEmpty();
    }

    private String createWorkspace() throws Exception {
        String body = mvc.perform(post("/api/negotiations").with(member()).with(csrf().asHeader())
                        .contentType(MediaType.APPLICATION_JSON).content("{\"supplier_id\":\"SUP-0060\",\"rfq_id\":\"RFQ-2026-002\"}"))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        return JSON.readTree(body).path("negotiation_id").asText();
    }

    private RequestPostProcessor member() {
        var now = Instant.now();
        var member = new MembershipRepository.Member(owner, "session@example.com", "세션 회원", "구매팀", "매니저",
                "unused", "USER", "ACTIVE", now, now);
        when(members.byId(owner)).thenReturn(member);
        return authentication(AuthSessionService.authentication(member.identity()));
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class FlowConfiguration {
        @Bean MemoryNegotiationRepository memoryNegotiationRepository() { return new MemoryNegotiationRepository(); }
        @Bean NegotiationService negotiationService(MemoryNegotiationRepository repository, GenerationGateway gateway,
                SupplierLookupGateway suppliers, LegalReviewRequestFactory reviews, JsonMapper mapper, AgentProperties properties) {
            var transactions = new TransactionTemplate(new AbstractPlatformTransactionManager() {
                @Override protected Object doGetTransaction() { return new Object(); }
                @Override protected void doBegin(Object tx, TransactionDefinition definition) {}
                @Override protected void doCommit(DefaultTransactionStatus status) {}
                @Override protected void doRollback(DefaultTransactionStatus status) {}
            });
            NegotiationExportStore store = new NegotiationExportStore() {
                private final Map<String, byte[]> data = new HashMap<>();
                public void write(String key, byte[] bytes) { data.put(key, bytes.clone()); }
                public byte[] read(String key) { return data.get(key).clone(); }
            };
            return new NegotiationService(repository, transactions, gateway, suppliers, reviews, mapper,
                    new NegotiationRequests(repository, transactions, mapper, properties), store, properties);
        }
    }
}
