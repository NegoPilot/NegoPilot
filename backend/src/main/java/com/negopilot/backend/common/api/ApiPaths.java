package com.negopilot.backend.common.api;

/** Public API path prefixes. URLs carry no version; response bodies carry schema_version instead. */
public final class ApiPaths {
    private ApiPaths() {}

    public static final String ROOT = "/api";
    public static final String AUTH = ROOT + "/auth";
    public static final String ADMIN = ROOT + "/admin";
    public static final String AGENT_QUERIES = ROOT + "/agent/queries";
    public static final String CONVERSATIONS = ROOT + "/conversations";
    public static final String BUSINESS_CATALOG = ROOT + "/business/catalog";
    public static final String SUPPLIERS = ROOT + "/suppliers";
    public static final String NEGOTIATIONS = ROOT + "/negotiations";
    public static final String NEGOTIATION_EXPORTS = ROOT + "/negotiation-exports";
}
