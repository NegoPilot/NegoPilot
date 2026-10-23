package com.negopilot.backend.auth;

import java.util.Set;

final class MembershipPolicy {
    private MembershipPolicy() { }

    static boolean permitsSignIn(String status) {
        return status.equals("ACTIVE") || status.equals("PENDING");
    }

    static boolean isActiveAdmin(String role, String status) {
        return role.equals("ADMIN") && status.equals("ACTIVE");
    }

    static boolean permitsStatusChange(String oldStatus, String newStatus) {
        return newStatus.equals("ACTIVE") && Set.of("PENDING", "REJECTED", "DISABLED").contains(oldStatus)
                || newStatus.equals("REJECTED") && oldStatus.equals("PENDING")
                || newStatus.equals("DISABLED") && oldStatus.equals("ACTIVE");
    }
}
