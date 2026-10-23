"""Read-only directory SQL. Kept separate from conversational search/top-N ranking."""

from collections.abc import Callable

from fastmcp.exceptions import ToolError

# Identifiers and directions below come exclusively from these allowlists.
SUPPLIER_SORTS = {
    "name": 'lower(name) COLLATE "C"',
    "region": 'lower(region) COLLATE "C"',
    "founded_year": "founded_year",
    "monthly_capacity": "max_monthly_capacity",
    "defect_ppm": "defect_ppm",
    "on_time_delivery_rate": "on_time_rate",
    "audit_grade": 'audit_grade COLLATE "C"',
    "years_dealing": "years_dealing",
    "payment_days": "payment_days",
    "raw_material_ratio": "raw_material_ratio",
    "latest_quote_date": "latest_quote_date",
    "latest_quote_price": "latest_quote_price",
}
QUOTE_SORTS = {"quoted_at": "quoted_at", "unit_price_krw": "unit_price"}


def _text(value: str | None, name: str, *, blank_absent: bool = False) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"[INVALID_FILTER] {name} must be text")
    value = value.strip()
    if not value and blank_absent:
        return None
    if not value or len(value) > 100:
        raise ToolError(f"[INVALID_FILTER] {name} must contain 1..100 characters")
    return value


def _pagination(page: int, size: int, sort: str, direction: str, sorts: dict) -> str:
    if type(page) is not int or not 0 <= page <= 1_000_000 or type(size) is not int or not 1 <= size <= 100:
        raise ToolError("[INVALID_FILTER] page or size is out of range")
    if sort not in sorts or direction not in {"asc", "desc"}:
        raise ToolError("[INVALID_FILTER] unsupported ordering")
    return f"{sorts[sort]} {direction.upper()} NULLS LAST"


def _capability_filters(process, material, minimum, alias):
    conditions, params = [], []
    for name, value in (("process", process), ("material", material)):
        if value is not None:
            conditions.append(f"{alias}.{name} = %s")
            params.append(value)
    if minimum is not None:
        conditions.append(f"{alias}.monthly_capacity >= %s")
        params.append(minimum)
    return conditions, params


def _metadata(items, total, page, size, sort, direction):
    return {"items": items, "total": total, "page": page, "size": size,
            "returned_count": len(items), "total_pages": (total + size - 1) // size,
            "sort": sort, "direction": direction}


def list_suppliers(query: Callable, *, q=None, process=None, material=None, region=None,
                   iatf_required=False, iso14001_required=False, min_monthly_capacity=None,
                   page=0, size=20, sort="name", direction="asc") -> dict:
    """Filter at supplier level. Count and page share one PostgreSQL statement snapshot."""
    order = _pagination(page, size, sort, direction, SUPPLIER_SORTS)
    q = _text(q, "q", blank_absent=True)
    process, material, region = (_text(process, "process"), _text(material, "material"), _text(region, "region"))
    if type(iatf_required) is not bool or type(iso14001_required) is not bool:
        raise ToolError("[INVALID_FILTER] certification flags must be booleans")
    if min_monthly_capacity is not None and (type(min_monthly_capacity) is not int or
                                           not 1 <= min_monthly_capacity <= 10_000_000):
        raise ToolError("[INVALID_FILTER] invalid capacity")
    if sort == "latest_quote_price" and (process is None or material is None):
        raise ToolError("[INVALID_FILTER] price sorting requires process and material")
    cap_filters, cap_params = _capability_filters(process, material, min_monthly_capacity, "c")
    cap_where = " AND " + " AND ".join(cap_filters) if cap_filters else ""
    quote_filters, quote_params = _capability_filters(process, material, None, "qt")
    quote_where = " AND " + " AND ".join(quote_filters) if quote_filters else ""
    conditions, filters = ["s.status = 'ACTIVE'"], []
    if q is not None:
        # % and _ are ordinary user input, not wildcard commands. ! is the escape itself.
        pattern = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        conditions.append("(s.name ILIKE %s ESCAPE '!' OR s.supplier_id ILIKE %s ESCAPE '!')")
        filters.extend((pattern, pattern))
    if region is not None:
        conditions.append("s.region = %s")
        filters.append(region)
    if iatf_required:
        conditions.append("s.iatf16949")
    if iso14001_required:
        conditions.append("s.iso14001")
    if cap_filters:
        conditions.append(f"EXISTS (SELECT 1 FROM capabilities c WHERE c.supplier_id = s.supplier_id{cap_where})")
        filters.extend(cap_params)
    sql = f"""
        WITH filtered AS MATERIALIZED (
            SELECT s.supplier_id, s.name, s.region, s.source, s.founded_year, s.employees,
                   s.iatf16949, s.iso14001, s.certification_source, s.capability_source,
                   s.quality_source, s.updated_at,
                   ql.defect_ppm, ql.on_time_rate, ql.audit_grade, ql.years_dealing,
                   ct.payment_days, ct.price_linkage, ct.raw_material_ratio, ct.auto_renewal,
                   COALESCE(caps.capabilities, '[]'::jsonb) AS matched_capabilities,
                   caps.max_monthly_capacity,
                   latest.quote AS latest_quote, latest.quoted_at AS latest_quote_date,
                   latest.unit_price AS latest_quote_price,
                   ARRAY(SELECT DISTINCT f.source FROM financials f
                         WHERE f.supplier_id = s.supplier_id ORDER BY f.source) AS financial_sources,
                   ct.supplier_id IS NOT NULL AS has_contract,
                   EXISTS(SELECT 1 FROM quotes qt WHERE qt.supplier_id = s.supplier_id) AS has_quotes
            FROM suppliers s
            LEFT JOIN quality ql USING (supplier_id)
            LEFT JOIN contracts ct USING (supplier_id)
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(jsonb_build_object('process', c.process, 'material', c.material,
                           'monthly_capacity', c.monthly_capacity) ORDER BY c.process COLLATE "C", c.material COLLATE "C") AS capabilities,
                       max(c.monthly_capacity) AS max_monthly_capacity
                FROM capabilities c WHERE c.supplier_id = s.supplier_id{cap_where}
            ) caps ON TRUE
            LEFT JOIN LATERAL (
                SELECT jsonb_build_object('quote_id', qt.quote_id, 'process', qt.process,
                         'material', qt.material, 'unit_price', qt.unit_price, 'quoted_at', qt.quoted_at) AS quote,
                       qt.quoted_at, qt.unit_price
                FROM quotes qt WHERE qt.supplier_id = s.supplier_id{quote_where}
                ORDER BY qt.quoted_at DESC, qt.quote_id DESC LIMIT 1
            ) latest ON TRUE
            WHERE {' AND '.join(conditions)}
        ), paged AS (
            SELECT *, row_number() OVER (ORDER BY {order}, supplier_id COLLATE "C" ASC) AS ordinal
            FROM filtered ORDER BY {order}, supplier_id COLLATE "C" ASC LIMIT %s OFFSET %s
        )
        SELECT (SELECT count(*) FROM filtered) AS total,
               COALESCE((SELECT jsonb_agg(to_jsonb(p) - 'ordinal' ORDER BY ordinal) FROM paged p), '[]'::jsonb) AS items
    """
    result = query(sql, cap_params + quote_params + filters + [size, page * size])[0]
    return _metadata(result["items"], result["total"], page, size, sort, direction)


def list_quotes(query: Callable, *, supplier_id, process=None, material=None,
                page=0, size=20, sort="quoted_at", direction="desc") -> dict:
    order = _pagination(page, size, sort, direction, QUOTE_SORTS)
    process, material = _text(process, "process"), _text(material, "material")
    if sort == "unit_price_krw" and (process is None or material is None):
        raise ToolError("[INVALID_FILTER] price sorting requires process and material")
    clauses, params = _capability_filters(process, material, None, "qt")
    where = " AND " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        WITH active AS MATERIALIZED (
            SELECT supplier_id FROM suppliers WHERE supplier_id = %s AND status = 'ACTIVE'
        ), filtered AS MATERIALIZED (
            SELECT qt.quote_id, qt.process, qt.material, qt.unit_price, qt.quoted_at
            FROM quotes qt JOIN active USING (supplier_id) WHERE TRUE{where}
        ), paged AS (
            SELECT *, row_number() OVER (ORDER BY {order}, quote_id DESC) AS ordinal
            FROM filtered ORDER BY {order}, quote_id DESC LIMIT %s OFFSET %s
        )
        SELECT EXISTS(SELECT 1 FROM active) AS supplier_exists,
               (SELECT count(*) FROM filtered) AS total,
               COALESCE((SELECT jsonb_agg(to_jsonb(p) - 'ordinal' ORDER BY ordinal) FROM paged p), '[]'::jsonb) AS items
    """
    result = query(sql, [supplier_id] + params + [size, page * size])[0]
    return {"supplier_id": supplier_id, "supplier_exists": result["supplier_exists"],
            **_metadata(result["items"], result["total"], page, size, sort, direction)}
