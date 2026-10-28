import hashlib

import pytest
from fastapi.testclient import TestClient

from trilha.api import create_app
from trilha.db import Base
from trilha.security import Principal

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
TEXT = """TRILHA CCB V1
Contrato: DEMO-001
Principal: 1200.00
Valor da parcela: 400.00
Parcelas: 3
Primeiro vencimento: 2026-01-31
"""
FACTS = {
    "contract_ref": "DEMO-001",
    "principal": "1200.00",
    "installment_amount": "400.00",
    "installments": 3,
    "first_due_on": "2026-01-31",
}


@pytest.fixture
def app(tmp_path):
    credentials = {
        hashlib.sha256(key.encode()).hexdigest(): Principal(tenant, role)
        for key, tenant, role in [
            ("operator", TENANT, "operator"),
            ("reviewer", TENANT, "reviewer"),
            ("reader", TENANT, "reader"),
            ("other", OTHER, "reviewer"),
        ]
    }
    app = create_app("sqlite:///" + (tmp_path / "test.db").as_posix(), credentials)
    Base.metadata.create_all(app.state.engine)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer operator"
        yield client
