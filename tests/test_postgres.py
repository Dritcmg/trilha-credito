"""Real PG tests: never substitute SQLite for these locking/constraint assertions."""

import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from tests.conftest import TEXT
from tests.test_api import payload
from trilha.api import create_app
from trilha.db import Audit, Document, Job, Payment, Reconciliation
from trilha.domain import ReconcileInput
from trilha.security import Principal
from trilha.service import ingest, process_one, reconcile_document

pytestmark = pytest.mark.postgres
TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def pg(monkeypatch):
    url = os.environ.get("TRILHA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("TRILHA_TEST_DATABASE_URL is required for PostgreSQL integration")
    parsed = make_url(url)
    if not parsed.drivername.startswith("postgresql") or not parsed.database.endswith("_test"):
        raise RuntimeError("explicit_postgres_test_database_required")
    admin = create_engine(url)
    schema = "test_trilha_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TRILHA_TEST_SCHEMA", schema)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url, connect_args={"options": "-csearch_path=" + schema})
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory, engine
    engine.dispose()
    # Target is a freshly-created schema inside a database ending _test, never public.
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


def test_pg_concurrent_duplicate_intake_creates_one_job(pg):
    factory, _ = pg
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ingest(factory, TENANT, TEXT), range(16)))
    assert len({item[0]["id"] for item in results}) == 1
    assert sum(created for _, created in results) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1


def test_pg_workers_process_each_job_once(pg):
    factory, _ = pg
    for number in range(12):
        ingest(factory, TENANT, TEXT.replace("DEMO-001", f"DEMO-{number:03d}"))
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: process_one(factory), range(24)))
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(Job).where(Job.state == "done")) == 12
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Audit).where(Audit.action == "document.accepted")
            )
            == 12
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Document).where(Document.payload_text.is_not(None))
            )
            == 0
        )


def test_pg_concurrent_reconciliation_replays_without_double_payment(pg):
    factory, _ = pg
    doc, _ = ingest(factory, TENANT, TEXT)
    process_one(factory)
    data = ReconcileInput.model_validate(payload())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: reconcile_document(factory, TENANT, doc["id"], "same-key", data),
                range(16),
            )
        )
    assert len({item[0]["id"] for item in results}) == 1
    assert sum(created for _, created in results) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(Reconciliation)) == 1


def test_pg_transaction_interruption_is_recoverable(pg):
    factory, _ = pg
    doc, _ = ingest(factory, TENANT, TEXT)

    def fail():
        raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError):
        process_one(factory, before_commit=fail)
    assert process_one(factory)
    with factory() as session:
        assert session.get(Document, doc["id"]).state == "accepted"
        assert (
            session.scalar(
                select(func.count()).select_from(Audit).where(Audit.action == "document.accepted")
            )
            == 1
        )


def test_pg_api_uses_migrated_database_and_tenant_scope(pg):
    factory, engine = pg
    credentials = {hashlib.sha256(b"reviewer").hexdigest(): Principal(TENANT, "reviewer")}
    # Test-specific schema; do not expose connection credentials in test reports.
    url = engine.url.set(query={"options": "-csearch_path=" + os.environ["TRILHA_TEST_SCHEMA"]})
    app = create_app(url.render_as_string(hide_password=False), credentials)
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        response = client.post(
            "/v1/documents",
            json={"source_text": TEXT},
            headers={"Authorization": "Bearer reviewer"},
        )
        assert response.status_code == 202
        assert process_one(factory)
