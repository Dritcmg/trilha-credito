import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FACTS, TEXT
from trilha.domain import Facts
from trilha.extraction import pdf_text
from trilha.service import process_one


def test_calendar_boundary_is_422_not_500(client, app):
    response = client.post("/v1/documents", json={"source_text": "unknown"})
    document_id = response.json()["id"]
    process_one(app.state.factory)
    response = client.post(
        f"/v1/documents/{document_id}/review",
        json={**FACTS, "first_due_on": "9999-12-31"},
        headers={"Authorization": "Bearer reviewer"},
    )
    assert response.status_code == 422


def test_last_representable_single_installment_is_valid():
    result = Facts.model_validate({**FACTS, "installments": 1, "first_due_on": "9999-12-31"})
    assert result.first_due_on.year == 9999


def test_pdf_value_error_is_redacted(monkeypatch):
    def bad_reader(*_, **__):
        raise ValueError("confidential document fragment")

    monkeypatch.setattr("trilha.extraction.PdfReader", bad_reader)
    with pytest.raises(ValueError, match="^unreadable_pdf$"):
        pdf_text(b"%PDF-synthetic")


def test_readiness_fails_when_schema_missing(client, app):
    from trilha.db import Base

    Base.metadata.drop_all(app.state.engine)
    response = client.get("/health/ready")
    assert response.status_code == 503 and "sql" not in response.text.lower()


def test_worker_once_exit_is_failure_when_schema_missing(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "trilha.worker", "--once"],
        env={**os.environ, "DATABASE_URL": "sqlite:///" + (tmp_path / "empty.db").as_posix()},
        capture_output=True,
    )
    assert result.returncode == 1
    assert b"OperationalError" not in result.stderr


def test_duplicate_logical_contract_requires_review(client, app):
    first = client.post("/v1/documents", json={"source_text": TEXT}).json()["id"]
    process_one(app.state.factory)
    second = client.post("/v1/documents", json={"source_text": TEXT + "\n"}).json()["id"]
    process_one(app.state.factory)
    assert client.get("/v1/documents/" + first).json()["state"] == "accepted"
    assert client.get("/v1/documents/" + second).json()["issues"] == [
        "duplicate_contract_reference"
    ]
    response = client.post(
        f"/v1/documents/{second}/review", json=FACTS, headers={"Authorization": "Bearer reviewer"}
    )
    assert response.status_code == 409


def test_sync_database_does_not_block_http_event_loop(app, monkeypatch):
    from trilha import api

    original = api.ingest
    started, release = threading.Event(), threading.Event()

    def slow(*args):
        started.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(api, "ingest", slow)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(
            client.post,
            "/v1/documents",
            json={"source_text": TEXT},
            headers={"Authorization": "Bearer operator"},
        )
        assert started.wait(2)
        health = pool.submit(client.get, "/health/live")
        try:
            assert health.result(timeout=1).status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=2).status_code == 202
