package com.negopilot.backend.admin.law;

import com.negopilot.backend.admin.data.AdminDataController;
import com.negopilot.backend.admin.supplier.AdminSupplierService;
import com.negopilot.backend.agent.AgentHttpConfiguration;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.auth.AuthSessionService;
import com.negopilot.backend.auth.CurrentUser;
import com.negopilot.backend.auth.MembershipRepository;
import com.negopilot.backend.auth.SecurityConfiguration;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;
import tools.jackson.databind.json.JsonMapper;

@WebMvcTest(controllers = {AdminLawController.class, AdminDataController.class})
@Import({SecurityConfiguration.class, CurrentUser.class, AdminLawControllerTest.JsonConfiguration.class})
class AdminLawControllerTest {
    private static final String ROOT = "/api/v1/admin/laws";
    private static final String VERSION = "2026-01-01_" + "a".repeat(40);
    private static final String JOB = "74af7d24-2c46-47a9-a281-7b3a11b54482";
    @Autowired MockMvc mvc;
    @Autowired WebApplicationContext context;
    @Autowired JsonMapper mapper;
    @MockitoBean MembershipRepository members;
    @MockitoBean AdminLawGateway laws;
    @MockitoBean AdminSupplierService suppliers;

    @TestConfiguration(proxyBeanMethods = false)
    @EnableWebSecurity
    static class JsonConfiguration {
        @Bean JsonMapper contractJsonMapper() { return new AgentHttpConfiguration().contractJsonMapper(); }
    }
    @BeforeEach void setup() {
        mvc = MockMvcBuilders.webAppContextSetup(context).apply(springSecurity()).build();
        when(laws.read(anyString(), any())).thenReturn(mapper.createObjectNode().put("result", "read"));
        when(laws.latestRefresh(any())).thenReturn(mapper.createObjectNode().putNull("item"));
        when(laws.startRefresh(any())).thenReturn(mapper.createObjectNode().put("job_id", JOB).put("status", "RUNNING"));
        when(laws.refreshJob(any(), any())).thenReturn(mapper.createObjectNode().put("job_id", JOB).put("status", "SUCCEEDED"));
        when(suppliers.summary()).thenReturn(Map.of("total", 2));
    }
    private RequestPostProcessor principal(String role, String status) {
        UUID id = UUID.randomUUID(); Instant now = Instant.now();
        var member = new MembershipRepository.Member(id, "member@example.com", "회원", "구매", "매니저", "unused", role, status, now, now);
        when(members.byId(id)).thenReturn(member);
        return authentication(AuthSessionService.authentication(member.identity()));
    }
    private RequestPostProcessor admin() { return principal("ADMIN", "ACTIVE"); }

    @Test void adminRefreshCreatesAcceptedJobAndReadsLatestOrExactJob() throws Exception {
        mvc.perform(post(ROOT + "/refresh-jobs").with(admin()).with(csrf().asHeader()))
                .andExpect(status().isAccepted()).andExpect(jsonPath("$.job_id").value(JOB));
        mvc.perform(get(ROOT + "/refresh-jobs").with(admin()))
                .andExpect(status().isOk()).andExpect(jsonPath("$.item").value(org.hamcrest.Matchers.nullValue()));
        mvc.perform(get(ROOT + "/refresh-jobs/" + JOB.toUpperCase()).with(admin()))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("SUCCEEDED"));
        verify(laws).refreshJob(eq(UUID.fromString(JOB)), any());
    }
    @Test void requiresActiveAdminAndCsrfWithoutInvokingUpstream() throws Exception {
        mvc.perform(get(ROOT)).andExpect(status().isUnauthorized()).andExpect(jsonPath("$.error.code").value("UNAUTHENTICATED"));
        mvc.perform(get(ROOT).with(principal("USER", "ACTIVE"))).andExpect(status().isForbidden());
        mvc.perform(get(ROOT).with(principal("ADMIN", "PENDING"))).andExpect(status().isForbidden());
        mvc.perform(post(ROOT + "/refresh-jobs").with(admin()))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.error.code").value("CSRF_INVALID"));
        mvc.perform(post(ROOT + "/refresh-jobs").with(principal("USER", "ACTIVE")).with(csrf().asHeader())).andExpect(status().isForbidden());
        verifyNoInteractions(laws);
    }
    @Test void disabledMembershipCannotUseAnOldAdminIdentity() throws Exception {
        mvc.perform(get(ROOT).with(principal("ADMIN", "DISABLED")))
                .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.error.code").value("UNAUTHENTICATED"));
        verifyNoInteractions(laws);
    }
    @Test void refreshRejectsBodyQueryAndInvalidIds() throws Exception {
        for (String body : new String[]{"{}", "null", " ", "{\"force\":true}"}) {
            mvc.perform(post(ROOT + "/refresh-jobs").with(admin()).with(csrf().asHeader()).contentType(MediaType.APPLICATION_JSON).content(body))
                    .andExpect(status().isBadRequest()).andExpect(jsonPath("$.error.code").value("VALIDATION_ERROR"));
        }
        mvc.perform(post(ROOT + "/refresh-jobs?force=true").with(admin()).with(csrf().asHeader())).andExpect(status().isBadRequest());
        mvc.perform(get(ROOT + "/refresh-jobs?size=20").with(admin())).andExpect(status().isBadRequest());
        mvc.perform(get(ROOT + "/refresh-jobs/1-1-1-1-1").with(admin())).andExpect(status().isBadRequest());
        mvc.perform(get(ROOT + "/refresh-jobs/" + JOB + "?unused=1").with(admin())).andExpect(status().isBadRequest());
        verifyNoInteractions(laws);
    }
    @Test void readPathsValidateAndEncodeKoreanArticleExactlyOnce() throws Exception {
        mvc.perform(get(ROOT).with(admin())).andExpect(status().isOk());
        mvc.perform(get(ROOT + "/subcontract_act/versions").with(admin())).andExpect(status().isOk());
        mvc.perform(get(ROOT + "/subcontract_act/versions/" + VERSION + "/articles").with(admin())).andExpect(status().isOk());
        mvc.perform(get(ROOT + "/subcontract_act/versions/" + VERSION + "/articles/제16조의2").with(admin())).andExpect(status().isOk());
        verify(laws).read(eq("/internal/v1/admin/laws/subcontract_act/versions/" + VERSION + "/articles?offset=0&limit=50"), any());
        verify(laws).read(eq("/internal/v1/admin/laws/subcontract_act/versions/" + VERSION + "/articles/%EC%A0%9C16%EC%A1%B0%EC%9D%982"), any());
    }
    @Test void rejectsBadReadArgumentsBeforeProxy() throws Exception {
        for (String path : new String[]{
                ROOT + "?size=1", ROOT + "/INVALID/versions", ROOT + "/subcontract_act/versions/bad/articles",
                ROOT + "/subcontract_act/versions/" + VERSION + "/articles/not-an-article",
                ROOT + "/subcontract_act/versions/" + VERSION + "/articles?offset=-1",
                ROOT + "/subcontract_act/versions/" + VERSION + "/articles?limit=101",
                ROOT + "/subcontract_act/versions/" + VERSION + "/articles?offset=1&offset=2"}) {
            mvc.perform(get(path).with(admin())).andExpect(status().isBadRequest());
        }
        verifyNoInteractions(laws);
    }
    @Test void knownRefreshErrorKeepsSafeStatusAndEnvelope() throws Exception {
        when(laws.startRefresh(any())).thenThrow(ApiException.of(UUID.randomUUID(), 409, "REFRESH_BUSY", "Law refresh is already running", true));
        mvc.perform(post(ROOT + "/refresh-jobs").with(admin()).with(csrf().asHeader())).andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("REFRESH_BUSY")).andExpect(jsonPath("$.error.retryable").value(true));
    }
    @Test void summaryShowsUnavailableAsNullIndependentlyOfAvailableSource() throws Exception {
        when(laws.read(anyString(), any())).thenThrow(ApiException.unavailable(null));
        var first = mvc.perform(get("/api/v1/admin/data/summary").with(admin())).andExpect(status().isOk()).andReturn();
        var body = mapper.readTree(first.getResponse().getContentAsString());
        assertThat(body.at("/suppliers/status").stringValue()).isEqualTo("AVAILABLE");
        assertThat(body.at("/suppliers/data/total").intValue()).isEqualTo(2);
        assertThat(body.at("/laws/status").stringValue()).isEqualTo("UNAVAILABLE");
        assertThat(body.at("/laws/data").isNull()).isTrue();
        assertThat(body.at("/laws/error_code").stringValue()).isEqualTo("UPSTREAM_UNAVAILABLE");
        Instant.parse(body.get("checked_at").stringValue());
        when(suppliers.summary()).thenThrow(ApiException.unavailable(null));
        var second = mvc.perform(get("/api/v1/admin/data/summary").with(admin())).andExpect(status().isOk()).andReturn();
        var unavailable = mapper.readTree(second.getResponse().getContentAsString());
        assertThat(unavailable.at("/suppliers/data").isNull()).isTrue();
        assertThat(unavailable.at("/laws/data").isNull()).isTrue();
    }
    @Test void summaryIsAdminOnlyAndRejectsQueryParameters() throws Exception {
        mvc.perform(get("/api/v1/admin/data/summary").with(principal("USER", "ACTIVE"))).andExpect(status().isForbidden());
        mvc.perform(get("/api/v1/admin/data/summary?force=true").with(admin())).andExpect(status().isBadRequest());
        verifyNoInteractions(laws, suppliers);
    }
}
