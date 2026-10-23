"""Internal route paths called only by Spring. URLs carry no version; responses carry schema_version."""

INTERNAL_ROOT = "/internal"
QUERY_EXECUTIONS = INTERNAL_ROOT + "/query-executions"
ADMIN_LAWS = INTERNAL_ROOT + "/admin/laws"
