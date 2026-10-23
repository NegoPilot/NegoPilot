package com.negopilot.backend.agent;
import com.negopilot.backend.auth.MembershipRepository;
import com.negopilot.backend.auth.SecurityConfiguration;
import com.negopilot.backend.auth.AuthSessionService;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;
import tools.jackson.databind.json.JsonMapper;

@EnableWebSecurity
@org.springframework.boot.context.properties.EnableConfigurationProperties(AgentProperties.class)
@WebMvcTest(controllers = LegalReviewController.class)
@Import({SecurityConfiguration.class, AgentHttpConfiguration.class})
class LegalReviewControllerTest {
    @Autowired MockMvc mvc;
    @Autowired WebApplicationContext context;
    @Autowired JsonMapper mapper;
    @MockitoBean MembershipRepository members;
    @MockitoBean GenerationGateway gateway;
    @BeforeEach void setup() { mvc = MockMvcBuilders.webAppContextSetup(context).apply(springSecurity()).build(); }
    private RequestPostProcessor member(String status) {
        var id=UUID.randomUUID(); var now=Instant.now();
        var member=new MembershipRepository.Member(id,"qa@example.com","QA","구매","직원","unused","USER",status,now,now);
        when(members.byId(id)).thenReturn(member);
        return authentication(AuthSessionService.authentication(member.identity()));
    }
    @Test void authenticatedReviewKeepsStructuredFacts() throws Exception {
        when(gateway.review(any(),any())).thenAnswer(invocation -> invocation.getArgument(1));
        mvc.perform(post("/api/legal-reviews").with(member("ACTIVE")).with(csrf().asHeader())
            .contentType(MediaType.APPLICATION_JSON).content("{\"buyer_size\":{\"independence_met\":null,\"average_revenue_eok\":120.01},\"price_linkage\":false}"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.offer.price_linkage").value(false))
            .andExpect(jsonPath("$.offer.buyer_size.average_revenue_eok").value(120.01))
            .andExpect(jsonPath("$.offer.buyer_size.independence_met").value(org.hamcrest.Matchers.nullValue()));
    }
    @Test void rejectsUnauthenticatedPendingAndMissingCsrf() throws Exception {
        mvc.perform(post("/api/legal-reviews").with(csrf().asHeader()).contentType(MediaType.APPLICATION_JSON).content("{}"))
            .andExpect(status().isUnauthorized());
        mvc.perform(post("/api/legal-reviews").with(member("PENDING")).with(csrf().asHeader()).contentType(MediaType.APPLICATION_JSON).content("{}"))
            .andExpect(status().isForbidden());
        mvc.perform(post("/api/legal-reviews").with(member("ACTIVE")).contentType(MediaType.APPLICATION_JSON).content("{}"))
            .andExpect(status().isForbidden());
        verifyNoInteractions(gateway);
    }
}
