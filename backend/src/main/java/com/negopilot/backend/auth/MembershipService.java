package com.negopilot.backend.auth;

import com.negopilot.backend.common.api.ApiException;

import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

@Service
final class MembershipService {
    private enum Field { STATUS, ROLE }

    private final MembershipRepository members;
    private final CurrentUser current;
    private final AuthSessionService sessions;
    private final TransactionTemplate transactions;
    MembershipService(MembershipRepository members, CurrentUser current, AuthSessionService sessions, TransactionTemplate transactions) {
        this.members = members; this.current = current; this.sessions = sessions; this.transactions = transactions;
    }
    MembershipRepository.Page<MemberView> members(String search, String role, String status, String page, String size) {
        current.requireAdmin();
        var query = MembershipQueryValidator.members(search, role, status, page, size);
        return members.members(query.search(), query.role(), query.status(), query.page(), query.size());
    }

    MembershipRepository.Page<MembershipRepository.AccessLog> accessLogs(
            String userId, String event, String from, String to, String success, String page, String size) {
        current.requireAdmin();
        var query = MembershipQueryValidator.accessLogs(userId, event, from, to, success, page, size);
        return members.accessLogs(query.userId(), query.event(), query.from(), query.to(), query.success(), query.page(), query.size());
    }

    MemberView detail(UUID id) {
        current.requireAdmin();
        return required(id).view();
    }
    MemberView changeStatus(UUID id, String status) { return change(id, Field.STATUS, status); }

    MemberView changeRole(UUID id, String role) { return change(id, Field.ROLE, role); }

    private MemberView change(UUID id, Field field, String value) {
        UUID actorId = current.requireAdmin().userId();
        if (actorId.equals(id)) throw ApiException.of(null, 409, "SELF_CHANGE_FORBIDDEN", "You cannot change your own membership.", false);
        return transactions.execute(tx -> {
            members.serializeChanges();
            // Recheck after taking the common lock. Two admins cannot concurrently remove each other.
            var actor = members.lock(actorId);
            if (actor == null || !MembershipPolicy.isActiveAdmin(actor.role(), actor.status())) throw CurrentUser.forbidden();
            var target = members.lock(id);
            if (target == null) throw missing();
            String oldValue = field == Field.STATUS ? target.status() : target.role();
            if (oldValue.equals(value)) return target.view();
            if (field == Field.STATUS) {
                if (!MembershipPolicy.permitsStatusChange(oldValue, value)) throw ApiException.of(null, 409, "INVALID_STATUS_TRANSITION", "Status transition is not allowed.", false);
            } else if (!target.status().equals("ACTIVE")) {
                throw ApiException.of(null, 409, "MEMBER_NOT_ACTIVE", "Role changes require an active member.", false);
            }
            boolean removesAdmin = MembershipPolicy.isActiveAdmin(target.role(), target.status())
                    && (field == Field.STATUS && !value.equals("ACTIVE") || field == Field.ROLE && !value.equals("ADMIN"));
            if (removesAdmin && members.activeAdminCount() <= 1) {
                throw ApiException.of(null, 409, "LAST_ADMIN_REQUIRED", "An active administrator must remain.", false);
            }
            if (field == Field.STATUS) members.changeStatus(id, value); else members.changeRole(id, value);
            if (field == Field.ROLE || value.equals("DISABLED") || value.equals("REJECTED")) sessions.revoke(id);
            members.audit(actorId, id, field == Field.STATUS ? "STATUS_CHANGED" : "ROLE_CHANGED", oldValue, value);
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
