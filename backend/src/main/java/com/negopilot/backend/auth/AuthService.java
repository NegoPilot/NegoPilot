package com.negopilot.backend.auth;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.UserIdentity;

import java.util.UUID;
import org.springframework.context.annotation.Profile;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

@Service
@Profile("!legacy-test")
final class AuthService {
    record AuthenticatedMember(UserIdentity identity, MemberView view) { }

    private final MembershipRepository members;
    private final PasswordEncoder passwords;
    private final TransactionTemplate transactions;
    private final String dummyHash;
    AuthService(MembershipRepository members, PasswordEncoder passwords, TransactionTemplate transactions) {
        this.members = members;
        this.passwords = passwords;
        this.transactions = transactions;
        this.dummyHash = passwords.encode("not-a-real-password-1!");
    }
    MemberView signup(AuthRequestValidator.Signup input) {
        String hash = passwords.encode(input.password());
        try { return transactions.execute(tx -> members.insert(input, hash, "USER", "PENDING").view()); }
        catch (DuplicateKeyException failure) {
            throw ApiException.of(null, 409, "EMAIL_ALREADY_REGISTERED", "Email is already registered.", false);
        }
    }
    AuthenticatedMember authenticate(AuthRequestValidator.Login input, String ip, String userAgent) {
        var found = members.byEmail(input.email());
        boolean matches = passwords.matches(input.password(), found == null ? dummyHash : found.passwordHash());
        MembershipRepository.Member result = null;
        if (matches && found != null) {
            result = transactions.execute(tx -> {
                var latest = members.lock(found.id());
                if (latest == null || !latest.passwordHash().equals(found.passwordHash())
                        || !MembershipPolicy.permitsSignIn(latest.status())) return null;
                members.log(latest.id(), "LOGIN_SUCCESS", ip, userAgent);
                return latest;
            });
        }
        if (result == null) {
            members.log(found == null ? null : found.id(), "LOGIN_FAILURE", ip, userAgent);
            throw ApiException.of(null, 401, "INVALID_CREDENTIALS", "Invalid email or password.", false);
        }
        return new AuthenticatedMember(result.identity(), result.view());
    }

    MemberView currentMember(UUID userId) {
        return members.byId(userId).view();
    }

    void recordLogout(UUID userId, String ip, String userAgent) {
        members.log(userId, "LOGOUT", ip, userAgent);
    }
}
