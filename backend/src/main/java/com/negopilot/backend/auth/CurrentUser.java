package com.negopilot.backend.auth;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.UserIdentity;

import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;

@Component
public final class CurrentUser {
    private final MembershipRepository members;
    CurrentUser(MembershipRepository members) { this.members = members; }

    public UserIdentity requireSignedIn() {
        var authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || !(authentication.getPrincipal() instanceof UserIdentity identity)) throw unauthorized();
        var member = members.byId(identity.userId());
        if (member == null || !MembershipPolicy.permitsSignIn(member.status())) throw unauthorized();
        return member.identity();
    }
    public UserIdentity requireActive() {
        UserIdentity identity = requireSignedIn();
        if (!identity.status().equals("ACTIVE")) throw forbidden();
        return identity;
    }
    public UserIdentity requireAdmin() {
        UserIdentity identity = requireActive();
        if (!MembershipPolicy.isActiveAdmin(identity.role(), identity.status())) throw forbidden();
        return identity;
    }
    static ApiException unauthorized() { return ApiException.of(null, 401, "UNAUTHENTICATED", "Authentication is required.", false); }
    static ApiException forbidden() { return ApiException.of(null, 403, "FORBIDDEN", "Access is denied.", false); }
}
