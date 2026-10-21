package com.negopilot.backend;

import java.util.Set;
import java.util.UUID;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

@Service
@Profile("!legacy-test")
final class MembershipService {
    private final MembershipRepository members;
    private final CurrentUser current;
    private final AuthSessionService sessions;
    private final TransactionTemplate transactions;
    MembershipService(MembershipRepository members, CurrentUser current, AuthSessionService sessions, TransactionTemplate transactions) {
        this.members = members; this.current = current; this.sessions = sessions; this.transactions = transactions;
    }
    MembershipRepository.MemberView detail(UUID id) {
        current.requireAdmin();
        return required(id).view();
    }
    MembershipRepository.MemberView change(UUID id, String field, String value) {
        UUID actorId = current.requireAdmin().userId();
        if (actorId.equals(id)) throw ApiException.of(null, 409, "SELF_CHANGE_FORBIDDEN", "You cannot change your own membership.", false);
        return transactions.execute(tx -> {
            members.serializeChanges();
            // Recheck after taking the common lock. Two admins cannot concurrently remove each other.
            var actor = members.lock(actorId);
            if (actor == null || !actor.status().equals("ACTIVE") || !actor.role().equals("ADMIN")) throw CurrentUser.forbidden();
            var target = members.lock(id);
            if (target == null) throw missing();
            String oldValue = field.equals("status") ? target.status() : target.role();
            if (oldValue.equals(value)) return target.view();
            if (field.equals("status")) {
                boolean allowed = value.equals("ACTIVE") && Set.of("PENDING", "REJECTED", "DISABLED").contains(oldValue)
                        || value.equals("REJECTED") && oldValue.equals("PENDING")
                        || value.equals("DISABLED") && oldValue.equals("ACTIVE");
                if (!allowed) throw ApiException.of(null, 409, "INVALID_STATUS_TRANSITION", "Status transition is not allowed.", false);
            } else if (!target.status().equals("ACTIVE")) {
                throw ApiException.of(null, 409, "MEMBER_NOT_ACTIVE", "Role changes require an active member.", false);
            }
            boolean removesAdmin = target.role().equals("ADMIN") && target.status().equals("ACTIVE")
                    && (field.equals("status") && !value.equals("ACTIVE") || field.equals("role") && !value.equals("ADMIN"));
            if (removesAdmin && members.activeAdminCount() <= 1) {
                throw ApiException.of(null, 409, "LAST_ADMIN_REQUIRED", "An active administrator must remain.", false);
            }
            if (field.equals("status")) members.changeStatus(id, value); else members.changeRole(id, value);
            if (field.equals("role") || value.equals("DISABLED") || value.equals("REJECTED")) sessions.revoke(id);
            members.audit(actorId, id, field.equals("status") ? "STATUS_CHANGED" : "ROLE_CHANGED", oldValue, value);
            return required(id).view();
        });
    }
    private MembershipRepository.Member required(UUID id) {
        var member = members.byId(id);
        if (member == null) throw missing();
        return member;
    }
    private static ApiException missing() { return ApiException.of(null, 404, "MEMBER_NOT_FOUND", "Member was not found.", false); }
}
