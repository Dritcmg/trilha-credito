import io

import pytest
from reportlab.pdfgen import canvas
from sqlalchemy import func, select

from tests.conftest import FACTS, TEXT
from trilha.db import Audit, Document, Job, Payment, Reconciliation, database
from trilha.service import process_one


def accepted(client, app):
    response = client.post("/v1/documents", json={"source_text": TEXT})
    assert response.status_code in {200, 202}
    document_id = response.json()["id"]
    process_one(app.state.factory)
    return document_id


def payload(amount="400.00", ref="DEMO-P1"):
    return {
        "as_of": "2026-02-02",
        "settlements": [
            {"payment_ref": ref, "amount": amount, "installment_number": 1, "paid_on": "2026-02-01"}
        ],
    }


def test_end_to_end_intake_worker_and_reconciliation(client, app):
    document_id = accepted(client, app)
    doc = client.get("/v1/documents/" + document_id).json()
    assert doc["state"] == "accepted" and doc["facts"] == FACTS
    response = client.post(
        f"/v1/documents/{document_id}/reconciliations",
        json=payload(),
        headers={"Idempotency-Key": "run-1"},
    )
    assert response.status_code == 201
    assert response.json()["received_total"] == "400.00"
    assert response.json()["installments"][0]["state"] == "matched"
    audit = client.get(f"/v1/documents/{document_id}/audit").json()
    assert {item["action"] for item in audit} == {
        "document.queued",
        "document.accepted",
        "reconciliation.created",
    }
    with app.state.factory() as session:
        assert session.get(Document, document_id).payload_text is None


def test_document_deduplicates_and_tenants_are_independent(client, app):
    first = client.post("/v1/documents", json={"source_text": TEXT})
    duplicate = client.post("/v1/documents", json={"source_text": TEXT})
    other = client.post(
        "/v1/documents", json={"source_text": TEXT}, headers={"Authorization": "Bearer other"}
    )
    assert first.status_code == 202 and duplicate.status_code == 200
    assert first.json()["id"] == duplicate.json()["id"] != other.json()["id"]
    with app.state.factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 2


def test_no_auth_and_bad_auth(client):
    client.headers.clear()
    assert client.post("/v1/documents", json={"source_text": TEXT}).status_code == 401
    assert (
        client.post(
            "/v1/documents", json={"source_text": TEXT}, headers={"Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )


def test_reader_cannot_write(client):
    client.headers["Authorization"] = "Bearer reader"
    assert client.post("/v1/documents", json={"source_text": TEXT}).status_code == 403


@pytest.mark.parametrize("path", ["", "/audit", "/review", "/reconciliations"])
def test_cross_tenant_never_sees_document(client, app, path):
    document_id = accepted(client, app)
    client.headers["Authorization"] = "Bearer other"
    endpoint = "/v1/documents/" + document_id + path
    if path == "/review":
        response = client.post(endpoint, json=FACTS)
    elif path == "/reconciliations":
        response = client.post(endpoint, json=payload(), headers={"Idempotency-Key": "r1"})
    else:
        response = client.get(endpoint)
    assert response.status_code == 404


def test_unknown_requires_reviewer_and_cannot_be_reapproved(client, app):
    document_id = client.post("/v1/documents", json={"source_text": "unknown"}).json()["id"]
    process_one(app.state.factory)
    assert client.get("/v1/documents/" + document_id).json()["state"] == "needs_review"
    assert client.post(f"/v1/documents/{document_id}/review", json=FACTS).status_code == 403
    client.headers["Authorization"] = "Bearer reviewer"
    assert client.post(f"/v1/documents/{document_id}/review", json=FACTS).status_code == 200
    assert client.post(f"/v1/documents/{document_id}/review", json=FACTS).status_code == 409


def test_pending_cannot_reconcile(client):
    doc = client.post("/v1/documents", json={"source_text": TEXT}).json()["id"]
    assert (
        client.post(
            f"/v1/documents/{doc}/reconciliations",
            json=payload(),
            headers={"Idempotency-Key": "r1"},
        ).status_code
        == 409
    )


def test_idempotency_replay_and_conflict(client, app):
    doc = accepted(client, app)
    endpoint = f"/v1/documents/{doc}/reconciliations"
    first = client.post(endpoint, json=payload(), headers={"Idempotency-Key": "r1"})
    replay = client.post(endpoint, json=payload(), headers={"Idempotency-Key": "r1"})
    conflict = client.post(endpoint, json=payload("401.00"), headers={"Idempotency-Key": "r1"})
    assert first.status_code == 201 and replay.status_code == 200
    assert first.json() == replay.json() and conflict.status_code == 409
    with app.state.factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(Reconciliation)) == 1


def test_payment_reference_conflict_rolls_back_entire_request(client, app):
    doc = accepted(client, app)
    endpoint = f"/v1/documents/{doc}/reconciliations"
    client.post(endpoint, json=payload(), headers={"Idempotency-Key": "r1"})
    changed = payload("401.00")
    changed["settlements"].insert(0, {**changed["settlements"][0], "payment_ref": "DEMO-NEW"})
    assert client.post(endpoint, json=changed, headers={"Idempotency-Key": "r2"}).status_code == 409
    with app.state.factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1


def test_replay_payment_in_new_snapshot_not_double_counted(client, app):
    doc = accepted(client, app)
    endpoint = f"/v1/documents/{doc}/reconciliations"
    client.post(endpoint, json=payload(), headers={"Idempotency-Key": "r1"})
    response = client.post(endpoint, json=payload(), headers={"Idempotency-Key": "r2"})
    assert response.json()["received_total"] == "400.00"


def test_worker_transaction_rollback_and_retry(client, app):
    doc = client.post("/v1/documents", json={"source_text": TEXT}).json()["id"]

    def fail():
        raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError):
        process_one(app.state.factory, before_commit=fail)
    assert client.get("/v1/documents/" + doc).json()["state"] == "pending"
    with app.state.factory() as session:
        assert session.scalar(select(func.count()).select_from(Audit)) == 1
    assert process_one(app.state.factory) is True
    assert process_one(app.state.factory) is False


def test_poison_job_goes_to_review_after_three_failures(client, app):
    doc = client.post("/v1/documents", json={"source_text": TEXT}).json()["id"]

    def fail():
        raise RuntimeError("synthetic")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            process_one(app.state.factory, before_commit=fail)
    assert process_one(app.state.factory) is False
    assert client.get("/v1/documents/" + doc).json()["issues"] == ["processing_failed"]
    with app.state.factory() as session:
        assert session.get(Document, doc).payload_text is None


def test_real_pdf_text_is_extracted_and_processed(client, app):
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream)
    text = pdf.beginText(30, 800)
    for line in TEXT.splitlines():
        text.textLine(line)
    pdf.drawText(text)
    pdf.save()
    response = client.post(
        "/v1/documents", content=stream.getvalue(), headers={"Content-Type": "application/pdf"}
    )
    assert response.status_code == 202
    process_one(app.state.factory)
    assert client.get("/v1/documents/" + response.json()["id"]).json()["facts"] == FACTS


def test_body_limit_invalid_type_and_validation_redaction(client):
    assert (
        client.post(
            "/v1/documents", content=b"x" * 1048577, headers={"Content-Type": "application/pdf"}
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/v1/documents", content=b"abc", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    assert client.post("/v1/documents", json={"source_text": "x" * 16001}).status_code == 422
    response = client.post("/v1/documents", json={"source_text": 123, "secret": "sensitive"})
    assert "sensitive" not in response.text


def test_readiness_request_ids_metrics_and_log_redaction(client, app, caplog):
    caplog.set_level("INFO", logger="trilha.requests")
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    response = client.post("/v1/documents", json={"source_text": TEXT})
    assert response.headers["X-Request-ID"] and response.headers["Cache-Control"] == "no-store"
    assert "TRILHA CCB" not in caplog.text and "Bearer operator" not in caplog.text
    assert client.get("/metrics").status_code == 403
    client.headers["Authorization"] = "Bearer reviewer"
    assert "trilha_requests_total" in client.get("/metrics").text


def test_pending_job_survives_database_engine_restart(client, app):
    doc = client.post("/v1/documents", json={"source_text": TEXT}).json()["id"]
    url = str(app.state.engine.url)
    app.state.engine.dispose()
    engine, factory = database(url)
    assert process_one(factory) is True
    with factory() as session:
        assert session.get(Document, doc).state == "accepted"
    engine.dispose()
