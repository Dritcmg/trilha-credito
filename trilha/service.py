import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from trilha.db import Audit, Document, Job, Payment, Reconciliation
from trilha.domain import Facts, ReconcileInput, Settlement, reconcile
from trilha.extraction import extract


def doc_view(doc: Document) -> dict:
    return {
        "id": doc.id,
        "state": doc.state,
        "digest": doc.digest,
        "facts": doc.facts,
        "issues": doc.issues,
    }


def owned(session, tenant: str, document_id: str, *, lock=False) -> Document:
    query = select(Document).where(Document.id == document_id, Document.tenant_id == tenant)
    if lock:
        query = query.with_for_update()
    doc = session.scalar(query)
    if doc is None:
        raise HTTPException(404, "document_not_found")
    return doc


def ingest(factory, tenant: str, text: str) -> tuple[dict, bool]:
    digest = hashlib.sha256(text.encode()).hexdigest()
    with factory() as session:
        doc = session.scalar(
            select(Document).where(Document.tenant_id == tenant, Document.digest == digest)
        )
        if doc:
            return doc_view(doc), False
        try:
            doc = Document(tenant_id=tenant, digest=digest, payload_text=text)
            session.add(doc)
            session.flush()
            session.add_all(
                [
                    Job(document_id=doc.id, tenant_id=tenant),
                    Audit(document_id=doc.id, tenant_id=tenant, action="document.queued"),
                ]
            )
            session.commit()
            return doc_view(doc), True
        except IntegrityError:
            session.rollback()
            doc = session.scalar(
                select(Document).where(Document.tenant_id == tenant, Document.digest == digest)
            )
            if doc is None:
                raise
            return doc_view(doc), False


def process_one(factory, *, before_commit=None) -> bool:
    """Claim, parse and commit in ONE transaction; rollback leaves durable work pending.

    PostgreSQL workers use SKIP LOCKED. SQLite is for sequential smoke/tests only.
    No external effects occur inside the transaction. before_commit is a test seam.
    """
    job_id = None
    try:
        with factory.begin() as session:
            job = session.scalar(
                select(Job)
                .where(Job.state == "pending")
                .order_by(Job.created_at, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return False
            job_id = job.id
            doc = owned(session, job.tenant_id, job.document_id, lock=True)
            facts, issues = extract(doc.payload_text or "")
            if facts:
                duplicate = session.scalar(
                    select(Document.id).where(
                        Document.tenant_id == doc.tenant_id,
                        Document.contract_ref == facts["contract_ref"],
                        Document.id != doc.id,
                    )
                )
                if duplicate:
                    issues = ["duplicate_contract_reference"]
                else:
                    doc.contract_ref = facts["contract_ref"]
            doc.facts = facts
            doc.issues = issues
            doc.state = "accepted" if facts and not issues else "needs_review"
            doc.payload_text = None
            job.state = "done"
            job.attempts += 1
            session.add(
                Audit(tenant_id=job.tenant_id, document_id=doc.id, action="document." + doc.state)
            )
            if before_commit:
                before_commit()
        return True
    except Exception:
        if job_id is None:
            raise
        with factory.begin() as session:
            job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            # Another worker may have completed the rolled-back item before this lock.
            if job.state == "pending":
                job.attempts += 1
                if job.attempts >= 3:
                    job.state = "dead"
                    doc = owned(session, job.tenant_id, job.document_id, lock=True)
                    doc.state = "needs_review"
                    doc.issues = ["processing_failed"]
                    doc.payload_text = None
                    session.add(
                        Audit(
                            tenant_id=job.tenant_id,
                            document_id=doc.id,
                            action="document.processing_failed",
                        )
                    )
        raise


def approve(factory, tenant: str, document_id: str, facts: Facts) -> dict:
    with factory.begin() as session:
        doc = owned(session, tenant, document_id, lock=True)
        if doc.state != "needs_review":
            raise HTTPException(409, "only_needs_review_can_be_approved")
        duplicate = session.scalar(
            select(Document.id).where(
                Document.tenant_id == tenant,
                Document.contract_ref == facts.contract_ref,
                Document.id != doc.id,
            )
        )
        if duplicate:
            raise HTTPException(409, "contract_reference_conflict")
        doc.contract_ref = facts.contract_ref
        doc.facts = facts.model_dump(mode="json")
        doc.issues = []
        doc.state = "reviewed"
        session.add(Audit(tenant_id=tenant, document_id=doc.id, action="document.reviewed"))
        session.flush()
        return doc_view(doc)


def reconcile_document(
    factory, tenant: str, document_id: str, key: str, payload: ReconcileInput
) -> tuple[dict, bool]:
    encoded = json.dumps(
        {"document_id": document_id, **payload.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with factory.begin() as session:
        # Serializes reconciliation of the same document on PostgreSQL.
        doc = owned(session, tenant, document_id, lock=True)
        existing = session.scalar(
            select(Reconciliation).where(
                Reconciliation.tenant_id == tenant, Reconciliation.idempotency_key == key
            )
        )
        if existing:
            if existing.request_digest != digest:
                raise HTTPException(409, "idempotency_key_conflict")
            return {"id": existing.id, **existing.result}, False
        if doc.state not in {"accepted", "reviewed"}:
            raise HTTPException(409, "document_not_validated")
        facts = Facts.model_validate(doc.facts)
        for item in payload.settlements:
            if item.installment_number > facts.installments:
                raise HTTPException(422, "installment_out_of_range")
            previous = session.scalar(
                select(Payment).where(
                    Payment.tenant_id == tenant, Payment.payment_ref == item.payment_ref
                )
            )
            if previous:
                same = (
                    previous.document_id == doc.id
                    and previous.installment_number == item.installment_number
                    and previous.amount == item.amount
                    and previous.paid_on == item.paid_on
                )
                if not same:
                    raise HTTPException(409, "payment_reference_conflict")
            else:
                session.add(Payment(tenant_id=tenant, document_id=doc.id, **item.model_dump()))
        session.flush()
        payments = session.scalars(
            select(Payment).where(Payment.tenant_id == tenant, Payment.document_id == doc.id)
        ).all()
        normalized = [
            Settlement(
                payment_ref=p.payment_ref,
                installment_number=p.installment_number,
                amount=f"{p.amount:.2f}",
                paid_on=p.paid_on,
            )
            for p in payments
        ]
        result = reconcile(facts, normalized, payload.as_of)
        report = Reconciliation(
            tenant_id=tenant,
            document_id=doc.id,
            idempotency_key=key,
            request_digest=digest,
            result=result,
        )
        session.add_all(
            [report, Audit(tenant_id=tenant, document_id=doc.id, action="reconciliation.created")]
        )
        session.flush()
        return {"id": report.id, **result}, True
