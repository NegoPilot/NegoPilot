package com.negopilot.backend.common.http;

/** Python internal API paths that only Spring calls. URLs carry no version; responses carry schema_version. */
public final class InternalPaths {
    private InternalPaths() {}

    public static final String ROOT = "/internal";
    public static final String QUERY_EXECUTIONS = ROOT + "/query-executions";
    public static final String BUSINESS_CATALOG = ROOT + "/business/catalog";
    public static final String SUPPLIERS = ROOT + "/suppliers";
    public static final String ADMIN_LAWS = ROOT + "/admin/laws";
}
