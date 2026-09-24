import pytest

from holmes.plugins.toolsets.openobserve.openobserve import OpenObserveSearchLogs


@pytest.mark.parametrize(
    "sql",
    [
        "select * from app_logs",
        "SELECT count(*) FROM app_logs",
        "with errors as (select * from app_logs) select * from errors",
    ],
)
def test_validate_sql_allows_read_only_queries(sql):
    assert OpenObserveSearchLogs.validate_sql(sql) == sql


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "delete from app_logs",
        "select * from app_logs; delete from app_logs",
        "update app_logs set level = 'info'",
        "create table x(a int)",
    ],
)
def test_validate_sql_rejects_mutating_or_multi_statement_queries(sql):
    with pytest.raises(ValueError):
        OpenObserveSearchLogs.validate_sql(sql)
