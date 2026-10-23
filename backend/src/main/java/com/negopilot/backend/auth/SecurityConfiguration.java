package com.negopilot.backend.auth;

import com.negopilot.backend.UserIdentity;
import com.negopilot.backend.common.api.ApiPaths;

import jakarta.servlet.DispatcherType;
import org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.authorization.AuthorizationDecision;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.context.HttpSessionSecurityContextRepository;
import org.springframework.security.web.context.SecurityContextHolderFilter;
import org.springframework.security.web.csrf.CsrfException;
import org.springframework.security.web.csrf.HttpSessionCsrfTokenRepository;
import tools.jackson.databind.json.JsonMapper;

@Configuration(proxyBeanMethods = false)
@ConditionalOnWebApplication(type = ConditionalOnWebApplication.Type.SERVLET)
public class SecurityConfiguration {
    @Bean
    SecurityFilterChain applicationSecurity(HttpSecurity http, MembershipRepository members, JsonMapper mapper) throws Exception {
        var contexts = new HttpSessionSecurityContextRepository();
        return http
                .securityContext(context -> context.securityContextRepository(contexts).requireExplicitSave(true))
                .csrf(csrf -> csrf.csrfTokenRepository(new HttpSessionCsrfTokenRepository()))
                .authorizeHttpRequests(access -> access
                        .dispatcherTypeMatchers(DispatcherType.ERROR).permitAll()
                        .requestMatchers(HttpMethod.GET, "/health", ApiPaths.AUTH + "/csrf").permitAll()
                        .requestMatchers(HttpMethod.POST, ApiPaths.AUTH + "/signup", ApiPaths.AUTH + "/login").anonymous()
                        .requestMatchers(ApiPaths.AUTH + "/me", ApiPaths.AUTH + "/logout").hasAnyAuthority("STATUS_ACTIVE", "STATUS_PENDING")
                        .requestMatchers(ApiPaths.ADMIN + "/**").access((authentication, context) -> {
                            var principal = authentication.get().getPrincipal();
                            return new AuthorizationDecision(principal instanceof UserIdentity identity
                                    && MembershipPolicy.isActiveAdmin(identity.role(), identity.status()));
                        })
                        .requestMatchers(ApiPaths.ROOT + "/**").hasAuthority("STATUS_ACTIVE")
                        .anyRequest().denyAll())
                .exceptionHandling(errors -> errors
                        .authenticationEntryPoint((request, response, failure) -> SecurityJsonErrors.write(request, response,
                                mapper, 401, "UNAUTHENTICATED", "Authentication is required."))
                        .accessDeniedHandler((request, response, failure) -> SecurityJsonErrors.write(request, response,
                                mapper, 403, failure instanceof CsrfException ? "CSRF_INVALID" : "FORBIDDEN", "Access is denied.")))
                .formLogin(login -> login.disable())
                .httpBasic(basic -> basic.disable())
                .logout(logout -> logout.disable())
                .requestCache(cache -> cache.disable())
                .addFilterAfter(new SecurityUserRefreshFilter(members, mapper, contexts), SecurityContextHolderFilter.class)
                .build();
    }
}
