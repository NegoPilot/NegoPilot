package com.negopilot.backend.legalreview;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.negopilot.backend.auth.AuthSessionService;
import com.negopilot.backend.auth.CurrentUser;
import com.negopilot.backend.auth.MembershipRepository;
import com.negopilot.backend.auth.SecurityConfiguration;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@EnableWebSecurity
@WebMvcTest(controllers = LegalReviewUploadController.class)
@Import({SecurityConfiguration.class, CurrentUser.class})
class LegalReviewUploadControllerTest {
    private static final String BASE = "/api/legal-review-uploads";
    @Autowired MockMvc mvc;
    @Autowired WebApplicationContext context;
    @Autowired JsonMapper mapper;
    @MockitoBean MembershipRepository members;
    @MockitoBean LegalReviewUploadService service;
    UUID owner;

    @BeforeEach void setup() { mvc = MockMvcBuilders.webAppContextSetup(context).apply(springSecurity()).build(); }

    private RequestPostProcessor member(String status) {
        owner = UUID.randomUUID();
        var now = Instant.now();
        var member = new MembershipRepository.Member(owner, "qa@example.com", "QA", "구매", "직원", "unused", "USER", status, now, now);
        when(members.byId(owner)).thenReturn(member);
        return authentication(AuthSessionService.authentication(member.identity()));
    }

    private MockMultipartFile xlsx(String name) { return new MockMultipartFile("file", name, "application/octet-stream", new byte[]{1, 2, 3}); }

    @Test void uploadIsAcceptedForActiveMembers() throws Exception {
        ObjectNode view = mapper.createObjectNode().put("upload_id", "u-1").put("status", "PROCESSING");
        when(service.upload(any(), any(), any(), any(), any())).thenReturn(view);
        mvc.perform(multipart(BASE).file(xlsx("c:\\temp\\조건.xlsx")).with(member("ACTIVE")).with(csrf().asHeader()))
                .andExpect(status().isAccepted()).andExpect(jsonPath("$.upload_id").value("u-1"));
        verify(service).upload(eq(owner), eq("조건.xlsx"), any(), isNull(), any());

        // 협력사를 고르면 그 행만 검토한다. 형식이 틀리면 파일을 읽기 전에 거절한다
        mvc.perform(multipart(BASE).file(xlsx("조건.xlsx")).param("supplier_id", "SUP-0034").with(member("ACTIVE")).with(csrf().asHeader()))
                .andExpect(status().isAccepted());
        verify(service).upload(eq(owner), eq("조건.xlsx"), any(), eq("SUP-0034"), any());
        mvc.perform(multipart(BASE).file(xlsx("조건.xlsx")).param("supplier_id", "sup-34").with(member("ACTIVE")).with(csrf().asHeader()))
                .andExpect(status().isBadRequest());
        mvc.perform(multipart(BASE).file(xlsx("조건.xlsx")).param("supplier_id", "SUP-0034", "SUP-0060").with(member("ACTIVE")).with(csrf().asHeader()))
                .andExpect(status().isBadRequest());
        verify(service, times(2)).upload(any(), any(), any(), any(), any());
    }

    @Test void rejectsBadUploadsBeforeParsing() throws Exception {
        mvc.perform(multipart(BASE).file(xlsx("조건.csv")).with(member("ACTIVE")).with(csrf().asHeader())).andExpect(status().isBadRequest());
        mvc.perform(multipart(BASE).with(member("ACTIVE")).with(csrf().asHeader())).andExpect(status().isBadRequest());
        mvc.perform(multipart(BASE).file(new MockMultipartFile("file", "빈.xlsx", null, new byte[0])).with(member("ACTIVE")).with(csrf().asHeader()))
                .andExpect(status().isBadRequest());
        mvc.perform(multipart(BASE).file(xlsx("조건.xlsx")).param("supplier_name", "x").with(member("ACTIVE")).with(csrf().asHeader()))
                .andExpect(status().isBadRequest());
        mvc.perform(multipart(BASE).file(xlsx("조건.xlsx")).with(csrf().asHeader())).andExpect(status().isUnauthorized());
        mvc.perform(multipart(BASE).file(xlsx("조건.xlsx")).with(member("PENDING")).with(csrf().asHeader())).andExpect(status().isForbidden());
        mvc.perform(multipart(BASE).file(xlsx("조건.xlsx")).with(member("ACTIVE"))).andExpect(status().isForbidden());
        verifyNoInteractions(service);
    }

    @Test void readsValidateIdsAndParameters() throws Exception {
        UUID id = UUID.randomUUID();
        when(service.list(any(), anyInt())).thenReturn(mapper.createObjectNode());
        when(service.get(any(), any(), any())).thenReturn(mapper.createObjectNode());
        when(service.row(any(), any(), anyInt(), any())).thenReturn(mapper.createObjectNode());

        mvc.perform(get(BASE).with(member("ACTIVE"))).andExpect(status().isOk());
        verify(service).list(owner, 20);
        mvc.perform(get(BASE).param("limit", "50").with(member("ACTIVE"))).andExpect(status().isOk());
        mvc.perform(get(BASE).param("limit", "51").with(member("ACTIVE"))).andExpect(status().isBadRequest());
        mvc.perform(get(BASE + "/" + id).with(member("ACTIVE"))).andExpect(status().isOk());
        mvc.perform(get(BASE + "/not-a-uuid").with(member("ACTIVE"))).andExpect(status().isBadRequest());
        mvc.perform(get(BASE + "/" + id + "/rows/3").with(member("ACTIVE"))).andExpect(status().isOk());
        verify(service).row(eq(owner), eq(id), eq(3), any());
        mvc.perform(get(BASE + "/" + id + "/rows/1").with(member("ACTIVE"))).andExpect(status().isBadRequest());
        mvc.perform(get(BASE + "/" + id + "/rows/3").param("revision", "1").with(member("ACTIVE"))).andExpect(status().isBadRequest());
        mvc.perform(get(BASE + "/" + id).param("x", "1").with(member("ACTIVE"))).andExpect(status().isBadRequest());
        mvc.perform(get(BASE + "/" + id)).andExpect(status().isUnauthorized());
    }

    @Test void retryNeedsCsrfAndNoBody() throws Exception {
        UUID id = UUID.randomUUID();
        when(service.retry(any(), any(), anyInt(), any())).thenReturn(mapper.createObjectNode().put("status", "QUEUED"));
        mvc.perform(post(BASE + "/" + id + "/rows/2/retry").with(member("ACTIVE")).with(csrf().asHeader()))
                .andExpect(status().isAccepted()).andExpect(jsonPath("$.status").value("QUEUED"));
        verify(service).retry(eq(owner), eq(id), eq(2), any());
        mvc.perform(post(BASE + "/" + id + "/rows/2/retry").with(member("ACTIVE")).with(csrf().asHeader())
                .contentType(MediaType.APPLICATION_JSON).content("{}")).andExpect(status().isAccepted());
        mvc.perform(post(BASE + "/" + id + "/rows/2/retry").with(member("ACTIVE")).with(csrf().asHeader())
                .contentType(MediaType.APPLICATION_JSON).content("{\"changes\":{}}")).andExpect(status().isBadRequest());
        mvc.perform(post(BASE + "/" + id + "/rows/2/retry").with(member("ACTIVE"))).andExpect(status().isForbidden());
    }
}
