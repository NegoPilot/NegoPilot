package com.negopilot.backend;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import java.util.Map;
import java.util.Set;
import java.util.HashSet;
import org.springframework.beans.factory.annotation.Autowired;
import org.flywaydb.core.Flyway;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/** Owns a separate pool without publishing a DataSource bean or changing Boot's application pool. */
@Component
final class BusinessDataStore implements AutoCloseable {
    private final HikariDataSource pool;
    private final JdbcTemplate jdbc;
    private final TransactionTemplate transaction;

    @Autowired
    BusinessDataStore(Environment env) {
        this(Boolean.parseBoolean(env.getProperty("business.datasource.enabled", "false")),
                env.getProperty("business.datasource.url", ""),
                env.getProperty("business.datasource.username", ""),
                env.getProperty("business.datasource.password", ""));
    }

    BusinessDataStore(boolean enabled, String url, String username, String password) {
        if (!enabled) { pool = null; jdbc = null; transaction = null; return; }
        HikariConfig config = new HikariConfig();
        config.setJdbcUrl(url);
        config.setUsername(username);
        config.setPassword(password);
        config.setPoolName("business-data");
        config.setMaximumPoolSize(5);
        config.setMinimumIdle(0);
        config.setConnectionTimeout(5000);
        pool = new HikariDataSource(config);
        jdbc = new JdbcTemplate(pool);
        transaction = new TransactionTemplate(new DataSourceTransactionManager(pool));
        try {
            Integer count = jdbc.queryForObject("""
                    SELECT count(*) FROM information_schema.tables
                    WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
                      AND table_name IN ('suppliers','financials','capabilities','quality','contracts','quotes','oems','rfqs')
                    """, Integer.class);
            if (count == null || count != 8) {
                throw new IllegalStateException("Business database requires the existing eight business tables. Prepare an isolated seed database explicitly before enabling management. No automatic seed is run.");
            }
            verifyExistingColumns();
            Flyway.configure().dataSource(pool).locations("classpath:db/business")
                    .table("business_schema_history").baselineOnMigrate(true).baselineVersion("0")
                    .cleanDisabled(true).load().migrate();
            jdbc.execute("SELECT status,version,profile_origin_source,certification_source,capability_source,quality_source FROM suppliers LIMIT 0");
        } catch (RuntimeException failure) {
            pool.close();
            throw new IllegalStateException("Business database initialization failed. Check its existing tables and migration history. No automatic seed is run.");
        }
    }

    private void verifyExistingColumns() {
        Map<String, Set<String>> expected = Map.of(
                "suppliers", Set.of("supplier_id", "source", "corp_code", "stock_code", "induty_code", "name", "region", "employees", "revenue_eok", "founded_year", "biz_no_masked", "iatf16949", "iso14001"),
                "capabilities", Set.of("supplier_id", "process", "material", "monthly_capacity"),
                "quality", Set.of("supplier_id", "defect_ppm", "on_time_rate", "audit_grade", "years_dealing"),
                "contracts", Set.of("supplier_id", "payment_days", "price_linkage", "raw_material_ratio", "auto_renewal"),
                "quotes", Set.of("quote_id", "supplier_id", "process", "material", "unit_price", "quoted_at"),
                "financials", Set.of("supplier_id", "bsns_year", "fs_div", "source", "revenue", "operating_income", "net_income", "total_assets", "total_liabilities", "total_equity"),
                "oems", Set.of("oem_id", "name", "quality_std"),
                "rfqs", Set.of("rfq_id", "oem_id", "vehicle", "part_name", "process", "material", "annual_volume", "target_price", "required_cert", "due_date"));
        expected.forEach((table, columns) -> {
            Set<String> actual = new HashSet<>(jdbc.queryForList("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?", String.class, table));
            if (!actual.containsAll(columns)) throw new IllegalStateException("Business database schema does not match the expected business tables.");
        });
    }

    JdbcTemplate jdbc() {
        if (jdbc == null) throw ApiException.of(null, 503, "BUSINESS_DATA_UNAVAILABLE", "Business data is not configured.", true);
        return jdbc;
    }

    TransactionTemplate transaction() { jdbc(); return transaction; }
    boolean enabled() { return jdbc != null; }

    @Override public void close() { if (pool != null) pool.close(); }
}
