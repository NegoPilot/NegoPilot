package com.negopilot.backend.common.http;

/** Python internal API paths that only Spring calls. URLs carry no version; responses carry schema_version. */
public final class InternalPaths {
    private InternalPaths() {}

    public static final String ROOT = "/internal";
    public static final String QUERY_EXECUTIONS = ROOT + "/query-executions";
    public static final String LEGAL_REVIEWS = ROOT + "/legal-reviews";
    public static final String OFFER_PROPOSALS = ROOT + "/offer-proposals";
    public static final String OFFER_RENDER = ROOT + "/offer-proposals/render";
    public static final String BUSINESS_CATALOG = ROOT + "/business/catalog";
    public static final String SUPPLIERS = ROOT + "/suppliers";
}
