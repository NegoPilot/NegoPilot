package com.negopilot.backend.auth;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;

@Configuration(proxyBeanMethods = false)
@Profile("!legacy-test")
@EnableScheduling
class MembershipConfiguration {
    @Bean PasswordEncoder passwordEncoder() { return new BCryptPasswordEncoder(12); }
    // Authentication is handled by AuthService. Disable Boot's generated in-memory user.
    @Bean UserDetailsService noDefaultUsers() { return username -> { throw new UsernameNotFoundException("Authentication is handled by the session API."); }; }
    @Bean
    @Profile("!bootstrap-admin")
    MembershipLogRetention membershipLogRetention(MembershipRepository members) { return new MembershipLogRetention(members); }
    static final class MembershipLogRetention {
        private final MembershipRepository members;
        MembershipLogRetention(MembershipRepository members) { this.members = members; }
        @Scheduled(fixedDelayString = "PT24H", initialDelayString = "PT1M")
        void removeExpired() { members.deleteExpiredLogs(); }
    }
}
