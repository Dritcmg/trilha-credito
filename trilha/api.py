import json
import logging
import os
import threading
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from trilha.db import Audit, database
from trilha.domain import Facts, ReconcileInput
from trilha.extraction import MAX_DOCUMENT_BYTES, MAX_TEXT, pdf_text
from trilha.security import Principal, allow, configured_credentials, principal
from trilha.service import approve, doc_view, ingest, owned, reconcile_document

logger = logging.getLogger("trilha.requests")


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_text: Annotated[str, Field(strict=True, min_length=1, max_length=MAX_TEXT)]


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        messages, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > MAX_DOCUMENT_BYTES:
                return await JSONResponse({"detail": "document_too_large"}, 413)(
                    scope, receive, send
                )
            messages.append(message)
            if not message.get("more_body"):
                break
        position = 0

        async def replay():
            nonlocal position
            if position < len(messages):
                result = messages[position]
                position += 1
                return result
            return await receive()

        return await self.app(scope, replay, send)


def create_app(database_url=None, credentials=None):
    engine, factory = database(database_url or os.environ["DATABASE_URL"])

    @asynccontextmanager
    async def lifespan(_):
        yield
        engine.dispose()

    app = FastAPI(
        title="Trilha Credito",
        version="0.1.0",
        lifespan=lifespan,
        description="Laboratório pessoal com contratos sintéticos. Sem decisão de crédito.",
    )
    app.state.factory = factory
    app.state.engine = engine
    app.state.credentials = credentials if credentials is not None else configured_credentials()
    app.state.counts = Counter()
    lock = threading.Lock()

    @app.middleware("http")
    async def observe(request, call_next):
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        with lock:
            app.state.counts[(request.method, route, response.status_code)] += 1
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "route": route,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                }
            )
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(BodyLimit)

    @app.exception_handler(RequestValidationError)
    async def invalid(_, exc):
        return JSONResponse(
            {"detail": [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]}, 422
        )

    @app.exception_handler(IntegrityError)
    async def integrity(_, __):
        return JSONResponse({"detail": "concurrent_conflict_retry_with_same_key"}, 409)

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1 FROM documents LIMIT 1"))
        except Exception:
            raise HTTPException(503, "database_unavailable") from None
        return {"status": "ready"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(actor: Principal = Depends(principal)):
        allow(actor, "reviewer")
        with lock:
            counts = list(app.state.counts.items())
        rows = ["# TYPE trilha_requests_total counter"]
        for (method, route, status), count in sorted(counts):
            rows.append(
                f'trilha_requests_total{{method="{method}",route="{route}",'
                f'status="{status}"}} {count}'
            )
        return "\n".join(rows) + "\n"

    @app.post("/v1/documents")
    async def submit(request: Request, actor: Principal = Depends(principal)):
        allow(actor, "operator", "reviewer")
        content_type = request.headers.get("content-type", "").split(";")[0]
        if content_type == "application/pdf":
            try:
                source = await run_in_threadpool(pdf_text, await request.body())
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        elif content_type == "application/json":
            try:
                source = Source.model_validate_json(await request.body()).source_text
            except (ValidationError, ValueError):
                raise HTTPException(422, "invalid_document_input") from None
        else:
            raise HTTPException(415, "expected_json_or_pdf")
        result, created = await run_in_threadpool(ingest, factory, actor.tenant_id, source)
        return JSONResponse(result, 202 if created else 200)

    @app.get("/v1/documents/{document_id}")
    def get_document(document_id: uuid.UUID, actor: Principal = Depends(principal)):
        with factory() as session:
            return doc_view(owned(session, actor.tenant_id, str(document_id)))

    @app.post("/v1/documents/{document_id}/review")
    def review(document_id: uuid.UUID, facts: Facts, actor: Principal = Depends(principal)):
        allow(actor, "reviewer")
        return approve(factory, actor.tenant_id, str(document_id), facts)

    @app.post("/v1/documents/{document_id}/reconciliations")
    def settlement(
        document_id: uuid.UUID,
        payload: ReconcileInput,
        idempotency_key: Annotated[
            str, Header(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
        ],
        actor: Principal = Depends(principal),
    ):
        allow(actor, "operator", "reviewer")
        result, created = reconcile_document(
            factory, actor.tenant_id, str(document_id), idempotency_key, payload
        )
        return JSONResponse(result, 201 if created else 200)

    @app.get("/v1/documents/{document_id}/audit")
    def audit(
        document_id: uuid.UUID,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        actor: Principal = Depends(principal),
    ):
        with factory() as session:
            doc = owned(session, actor.tenant_id, str(document_id))
            rows = session.scalars(
                select(Audit)
                .where(Audit.tenant_id == actor.tenant_id, Audit.document_id == doc.id)
                .order_by(Audit.created_at, Audit.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return [
                {"id": row.id, "action": row.action, "created_at": row.created_at.isoformat()}
                for row in rows
            ]

    return app
