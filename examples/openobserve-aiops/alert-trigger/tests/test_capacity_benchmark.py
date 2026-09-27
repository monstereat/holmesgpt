import pytest

from benchmark_admission import percentile, validate_options, validate_test_database_url


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://aiops:secret@postgres:5432/aiops_test",
        "postgres://aiops:secret@postgres/aiops_test",
    ],
)
def test_benchmark_accepts_only_compose_test_database_identity(database_url):
    validate_test_database_url(database_url)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://aiops:secret@localhost:5432/aiops_test",
        "postgresql://aiops:secret@postgres:5432/aiops",
        "postgresql://runtime:secret@postgres:5432/aiops_test",
        "postgresql://aiops:secret@postgres:5433/aiops_test",
        "postgresql://aiops:secret@postgres:5432/aiops_test?sslmode=disable",
        "postgresql://aiops:secret@db.example.test/aiops_test",
    ],
)
def test_benchmark_refuses_other_database_identities(database_url):
    with pytest.raises(ValueError, match="only permits the Compose test database"):
        validate_test_database_url(database_url)


@pytest.mark.parametrize(
    ("requests", "concurrency", "capacity"),
    [(100, 16, 80), (2, 1, 1)],
)
def test_benchmark_accepts_bounded_parameters(requests, concurrency, capacity):
    validate_options(requests, concurrency, capacity)


@pytest.mark.parametrize(
    ("requests", "concurrency", "capacity"),
    [(1, 1, 0), (100, 65, 80), (100, 16, 100), (5001, 16, 80)],
)
def test_benchmark_rejects_unbounded_or_unmeasurable_parameters(requests, concurrency, capacity):
    with pytest.raises(ValueError):
        validate_options(requests, concurrency, capacity)


def test_percentiles_use_nearest_rank_and_report_milliseconds():
    assert percentile([0.005, 0.001, 0.003, 0.004, 0.002], 0.50) == 3.0
    assert percentile([0.005, 0.001, 0.003, 0.004, 0.002], 0.95) == 5.0


@pytest.mark.parametrize(("samples", "fraction"), [([], 0.5), ([0.1], 0), ([0.1], 1.1)])
def test_percentile_rejects_empty_samples_or_invalid_fraction(samples, fraction):
    with pytest.raises(ValueError):
        percentile(samples, fraction)
