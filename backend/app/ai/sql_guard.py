"""Validate SQL that an AI (or an engineer editing it) wants to run during an investigation.

Generated SQL is treated like user input from the internet. It runs only if it passes every check here, and then
only inside a sandbox (see services/queries.py) — the validator is the first of several layers, not the only one:

    1. parse (PostgreSQL dialect) — exactly one statement, and it must be a SELECT (CTEs and UNIONs allowed)
    2. no writes anywhere in the tree: INSERT/UPDATE/DELETE/MERGE, DDL, SELECT … INTO, FOR UPDATE locks, COPY, SET
    3. tables on an allowlist: the ShopStream application tables and pg_stat_activity — never OpsPilot's own
    4. functions on an allowlist: no pg_sleep, pg_read_file, dblink, set_config, lo_import, …
    5. a row limit: added when missing, lowered when above the maximum
    ── then, at execution: read-only transaction · SET ROLE opspilot_readonly · statement_timeout · rollback

The parse is done on the AST, not with regular expressions, so comments, quoting and casing cannot hide a
statement. Anything the parser does not understand is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

MAX_ROWS = 200
ALLOWED_TABLES = {"payments", "orders", "password_reset_requests", "pg_stat_activity"}
ALLOWED_SCHEMAS = {"", "shopstream", "pg_catalog"}
ALLOWED_FUNCTIONS = {
    "count", "sum", "avg", "min", "max", "now", "date_trunc", "date_part", "extract", "coalesce", "nullif", "round",
    "abs", "greatest", "least", "lower", "upper", "length", "left", "right", "substring", "to_char", "age",
    "percentile_cont", "percentile_disc", "string_agg", "array_agg", "bool_or", "bool_and", "count_if", "trim",
}  # fmt: skip
WRITE_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Copy,
    exp.Set, exp.Transaction, exp.Commit, exp.Rollback, exp.Grant, exp.Into, exp.Lock, exp.TruncateTable,
)  # fmt: skip


@dataclass
class Verdict:
    ok: bool
    sql: str  # as submitted
    safe_sql: str | None = None  # normalised SQL that would run, with the row limit applied
    violations: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return (node.sql_name() if hasattr(node, "sql_name") else type(node).__name__).lower()


def validate(sql: str) -> Verdict:
    verdict = Verdict(ok=False, sql=sql)
    text = sql.strip().rstrip(";").strip()
    if not text:
        verdict.violations.append("empty query")
        return verdict
    try:
        statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
    except ParseError as exc:
        verdict.violations.append(f"could not parse as PostgreSQL: {str(exc).splitlines()[0]}")
        return verdict
    if len(statements) != 1:
        verdict.violations.append(f"exactly one statement allowed, got {len(statements)}")
        return verdict
    tree = statements[0]

    if not isinstance(tree, exp.Select | exp.Union | exp.Intersect | exp.Except):
        verdict.violations.append(f"only SELECT queries are allowed (got {tree.key.upper()})")
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            verdict.violations.append(f"{node.key.upper()} is not allowed")

    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name, schema = table.name.lower(), (table.db or "").lower()
        if not name:
            verdict.violations.append("table-valued functions are not allowed")
            continue
        if name in ctes and not schema:
            continue
        qualified = f"{schema}.{name}" if schema else name
        if name not in ALLOWED_TABLES or schema not in ALLOWED_SCHEMAS:
            verdict.violations.append(f"table {qualified} is not on the allowlist")
        elif qualified not in verdict.tables:
            verdict.tables.append(qualified)

    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        if isinstance(func, exp.Anonymous) and name not in ALLOWED_FUNCTIONS:
            verdict.violations.append(f"function {name}() is not on the allowlist")
        elif name.startswith(("pg_", "lo_", "dblink", "set_config", "current_setting")):
            verdict.violations.append(f"function {name}() is not allowed")

    if verdict.violations:
        verdict.violations = list(dict.fromkeys(verdict.violations))
        return verdict

    limit = tree.args.get("limit")
    value = limit.expression if isinstance(limit, exp.Limit) else None
    if isinstance(tree, exp.Select) and (value is None or not value.is_int or int(value.name) > MAX_ROWS):
        tree = tree.limit(MAX_ROWS, copy=True)
    elif not isinstance(tree, exp.Select):
        tree = exp.select("*").from_(tree.subquery("q")).limit(MAX_ROWS)
    verdict.safe_sql = tree.sql(dialect="postgres", pretty=True)
    verdict.ok = True
    return verdict
