package com.negopilot.backend.auth;

import com.negopilot.backend.UserIdentity;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
@Profile("!legacy-test")
public class MembershipRepository {
    private final JdbcTemplate jdbc;
    MembershipRepository(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    public record Member(UUID id, String email, String name, String department, String jobTitle,
                  String passwordHash, String role, String status, Instant createdAt, Instant updatedAt) {
        public UserIdentity identity() { return new UserIdentity(id, role, status); }
        MemberView view() { return new MemberView(id, email, name, department, jobTitle, role, status, createdAt, updatedAt); }
        @Override public String toString() { return "Member[credentials redacted]"; }
    }
    record Page<T>(List<T> items, long total, int page, int size) { }
    record AccessLog(UUID id, UUID user_id, String event, Instant occurred_at, String ip, String user_agent) { }

    public Member byId(UUID id) { return one("SELECT * FROM app_users WHERE id=?", id); }
    Member byEmail(String email) { return one("SELECT * FROM app_users WHERE email=?", email); }
    Member lock(UUID id) { return one("SELECT * FROM app_users WHERE id=? FOR UPDATE", id); }
    private Member one(String sql, Object value) {
        List<Member> result = jdbc.query(sql, MembershipRepository::member, value);
        return result.isEmpty() ? null : result.getFirst();
    }
    private static Member member(ResultSet row, int ignored) throws SQLException {
        return new Member(row.getObject("id", UUID.class), row.getString("email"), row.getString("name"),
                row.getString("department"), row.getString("job_title"), row.getString("password_hash"),
                row.getString("role"), row.getString("status"), row.getTimestamp("created_at").toInstant(),
                row.getTimestamp("updated_at").toInstant());
    }
    Member insert(AuthRequestValidator.Signup input, String hash, String role, String status) {
        UUID id = UUID.randomUUID();
        jdbc.update("INSERT INTO app_users(id,email,name,department,job_title,password_hash,role,status) VALUES (?,?,?,?,?,?,?,?)",
                id, input.email(), input.name(), input.department(), input.jobTitle(), hash, role, status);
        return byId(id);
    }
    void serializeChanges() { jdbc.queryForObject("SELECT id FROM app_membership_guard WHERE id=1 FOR UPDATE", Integer.class); }
    long activeAdminCount() { return jdbc.queryForObject("SELECT count(*) FROM app_users WHERE role='ADMIN' AND status='ACTIVE'", Long.class); }
    void changeStatus(UUID id, String status) { jdbc.update("UPDATE app_users SET status=?,updated_at=clock_timestamp() WHERE id=?", status, id); }
    void changeRole(UUID id, String role) { jdbc.update("UPDATE app_users SET role=?,updated_at=clock_timestamp() WHERE id=?", role, id); }
    void audit(UUID actor, UUID target, String action, String oldValue, String newValue) {
        jdbc.update("INSERT INTO app_membership_audit(id,actor_id,target_id,action,old_value,new_value) VALUES (?,?,?,?,?,?)",
                UUID.randomUUID(), actor, target, action, oldValue, newValue);
    }
    void log(UUID userId, String event, String ip, String userAgent) {
        jdbc.update("INSERT INTO app_access_logs(id,user_id,event,ip,user_agent) VALUES (?,?,?,?,?)",
                UUID.randomUUID(), userId, event, truncate(ip, 64), truncate(userAgent, 512));
    }
    int deleteExpiredLogs() { return jdbc.update("DELETE FROM app_access_logs WHERE occurred_at < current_timestamp - interval '90 days'"); }
    Page<MemberView> members(String search, String role, String status, int page, int size) {
        StringBuilder where = new StringBuilder(" WHERE 1=1");
        List<Object> values = new ArrayList<>();
        if (search != null) {
            where.append(" AND (position(lower(?) in lower(name)) > 0 OR position(lower(?) in lower(email)) > 0)");
            values.add(search); values.add(search);
        }
        if (role != null) { where.append(" AND role=?"); values.add(role); }
        if (status != null) { where.append(" AND status=?"); values.add(status); }
        long total = jdbc.queryForObject("SELECT count(*) FROM app_users" + where, Long.class, values.toArray());
        values.add(size); values.add((long) page * size);
        List<MemberView> items = jdbc.query("SELECT * FROM app_users" + where + " ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                MembershipRepository::member, values.toArray()).stream().map(Member::view).toList();
        return new Page<>(items, total, page, size);
    }
    Page<AccessLog> accessLogs(UUID userId, String event, Instant from, Instant to, Boolean success, int page, int size) {
        StringBuilder where = new StringBuilder(" WHERE occurred_at >= current_timestamp - interval '90 days'");
        List<Object> values = new ArrayList<>();
        if (userId != null) { where.append(" AND user_id=?"); values.add(userId); }
        if (event != null) { where.append(" AND event=?"); values.add(event); }
        if (from != null) { where.append(" AND occurred_at>=?"); values.add(java.sql.Timestamp.from(from)); }
        if (to != null) { where.append(" AND occurred_at<?"); values.add(java.sql.Timestamp.from(to)); }
        if (success != null) where.append(success ? " AND event IN ('LOGIN_SUCCESS','LOGOUT')" : " AND event='LOGIN_FAILURE'");
        long total = jdbc.queryForObject("SELECT count(*) FROM app_access_logs" + where, Long.class, values.toArray());
        values.add(size); values.add((long) page * size);
        List<AccessLog> items = jdbc.query("SELECT * FROM app_access_logs" + where + " ORDER BY occurred_at DESC,id LIMIT ? OFFSET ?",
                (row, n) -> new AccessLog(row.getObject("id", UUID.class), row.getObject("user_id", UUID.class),
                        row.getString("event"), row.getTimestamp("occurred_at").toInstant(), row.getString("ip"), row.getString("user_agent")), values.toArray());
        return new Page<>(items, total, page, size);
    }
    private static String truncate(String value, int max) {
        if (value == null) return "";
        return value.substring(0, value.offsetByCodePoints(0, Math.min(max, value.codePointCount(0, value.length()))));
    }
}
