package com.negopilot.backend.negotiation;

import com.negopilot.backend.agent.AgentHttpConfiguration;
import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.auth.AuthSessionService;
import com.negopilot.backend.auth.CurrentUser;
import com.negopilot.backend.auth.MembershipRepository;
import com.negopilot.backend.auth.SecurityConfiguration;
import com.negopilot.backend.common.api.ApiException;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;
import static com.negopilot.backend.negotiation.NegotiationFixtures.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@EnableWebSecurity
@EnableConfigurationProperties(AgentProperties.class)
@WebMvcTest(controllers = NegotiationController.class)
@Import({SecurityConfiguration.class, CurrentUser.class, AgentHttpConfiguration.class})
class NegotiationControllerTest {
    @Autowired MockMvc mvc;
    @Autowired WebApplicationContext context;
    @MockitoBean MembershipRepository members;
    @MockitoBean NegotiationService service;
    @MockitoBean NegotiationMailService mails;
    final UUID owner = UUID.randomUUID();
    final UUID workspace = UUID.randomUUID();
    final UUID proposal = UUID.randomUUID();
    final UUID file = UUID.randomUUID();

    @BeforeEach void setup() { mvc = MockMvcBuilders.webAppContextSetup(context).apply(springSecurity()).build(); }

    private RequestPostProcessor member(String role, String status) {
        var now = Instant.now();
        var member = new MembershipRepository.Member(owner, "member@example.com", "담당자", "구매팀", "매니저",
                "unused", role, status, now, now);
        when(members.byId(owner)).thenReturn(member);
        return authentication(AuthSessionService.authentication(member.identity()));
    }

    @Test void everyReadRouteRequiresAuthentication() throws Exception {
        for (String path : new String[] {"/api/negotiations", "/api/negotiations/" + workspace,
                "/api/negotiations/" + workspace + "/reviews/1", proposalPath(), proposalPath() + "/external",
                "/api/negotiation-exports/" + file + "/file"})
            mvc.perform(get(path)).andExpect(status().isUnauthorized());
        verifyNoInteractions(service);
    }

    @Test void writesRequireCsrfAndActiveMembership() throws Exception {
        mvc.perform(post("/api/negotiations").with(member("USER", "ACTIVE"))
                .contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.error.code").value("CSRF_INVALID"));
        mvc.perform(post("/api/negotiations").with(member("USER", "PENDING")).with(csrf().asHeader())
                .contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isForbidden());
        verifyNoInteractions(service);
    }

    @Test void requestOwnerAlwaysComesFromSessionAndClientSenderIsRejected() throws Exception {
        when(service.create(eq(owner), any(), isNull(), any())).thenReturn(new NegotiationRequests.Result(201,
                object("{\"negotiation_id\":\"" + workspace + "\"}")));
        mvc.perform(post("/api/negotiations").with(member("USER", "ACTIVE")).with(csrf().asHeader())
                .contentType(MediaType.APPLICATION_JSON).content("{\"supplier_id\":\"SUP-0060\",\"rfq_id\":\"RFQ-2026-002\"}"))
                .andExpect(status().isCreated()).andExpect(jsonPath("$.negotiation_id").value(workspace.toString()));
        verify(service).create(eq(owner), any(), isNull(), any());
        mvc.perform(post("/api/negotiations/" + workspace + "/proposals").with(member("USER", "ACTIVE")).with(csrf().asHeader())
                .contentType(MediaType.APPLICATION_JSON).content("{\"review_revision\":1,\"offer\":{\"sender\":{\"name\":\"다른 사람\"}}}"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error.code").value("VALIDATION_ERROR"));
        verify(service, never()).propose(any(), any(), any(), any(), any());
    }

    @Test void administratorReceivesNotFoundForOtherOwnersResources() throws Exception {
        ApiException missing = ApiException.of(null, 404, "RESOURCE_NOT_FOUND", "찾을 수 없습니다.", false);
        when(service.get(any(), any(), any())).thenThrow(missing);
        when(service.getReview(any(), any(), anyInt(), any())).thenThrow(missing);
        when(service.getProposal(any(), any(), any(), any(), any())).thenThrow(missing);
        when(service.download(any(), any(), any())).thenThrow(missing);
        for (String path : new String[] {"/api/negotiations/" + workspace, "/api/negotiations/" + workspace + "/reviews/1",
                proposalPath(), "/api/negotiation-exports/" + file + "/file"})
            mvc.perform(get(path).with(member("ADMIN", "ACTIVE"))).andExpect(status().isNotFound())
                    .andExpect(jsonPath("$.error.code").value("RESOURCE_NOT_FOUND"));
    }

    @Test void duplicateIdempotencyHeaderAndUnknownOrRepeatedQueryParametersAreRejected() throws Exception {
        for (String key : new String[] {"not-a-uuid", ""})
            mvc.perform(post("/api/negotiations").with(member("USER", "ACTIVE")).with(csrf().asHeader())
                    .header("Idempotency-Key", key).contentType(MediaType.APPLICATION_JSON)
                    .content("{\"supplier_id\":\"SUP-0060\",\"rfq_id\":\"RFQ-2026-002\"}"))
                    .andExpect(status().isBadRequest());
        mvc.perform(post("/api/negotiations").with(member("USER", "ACTIVE")).with(csrf().asHeader())
                .header("Idempotency-Key", UUID.randomUUID(), UUID.randomUUID()).contentType(MediaType.APPLICATION_JSON)
                .content("{\"supplier_id\":\"SUP-0060\",\"rfq_id\":\"RFQ-2026-002\"}"))
                .andExpect(status().isBadRequest());
        for (String path : new String[] {"/api/negotiations?limit=51", "/api/negotiations?limit=1&limit=2",
                "/api/negotiations?owner_id=x", proposalPath() + "?revision=0"})
            mvc.perform(get(path).with(member("USER", "ACTIVE"))).andExpect(status().isBadRequest());
        verifyNoInteractions(service);
    }

    @Test void downloadReturnsOriginalBytesWithKoreanFilenameHeader() throws Exception {
        when(service.download(eq(owner), eq(file), any())).thenReturn(new NegotiationService.Download(
                new byte[] {1, 2, 3}, "application/pdf", "제안서_RFQ-2026-002_SUP-0060_v1.pdf"));
        mvc.perform(get("/api/negotiation-exports/" + file + "/file").with(member("USER", "ACTIVE")))
                .andExpect(status().isOk()).andExpect(content().bytes(new byte[] {1, 2, 3}))
                .andExpect(content().contentType("application/pdf"))
                .andExpect(header().string("Content-Disposition", org.hamcrest.Matchers.allOf(
                        org.hamcrest.Matchers.startsWith("attachment; filename=\""),
                        org.hamcrest.Matchers.containsString("filename*=UTF-8''%EC%A0%9C%EC%95%88%EC%84%9C_"))));
    }

    private String proposalPath() { return "/api/negotiations/" + workspace + "/proposals/" + proposal; }
}
