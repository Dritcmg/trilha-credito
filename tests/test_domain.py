from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tests.conftest import FACTS, TEXT
from trilha.domain import Facts, ReconcileInput, Settlement, due_date, reconcile
from trilha.extraction import extract, pdf_text


def payment(amount="400.00", number=1, ref="DEMO-P1", day="2026-02-01"):
    return Settlement(amount=amount, installment_number=number, payment_ref=ref, paid_on=day)


@pytest.mark.parametrize(
    ("first", "offset", "expected"),
    [
        ("2026-01-31", 1, "2026-02-28"),
        ("2026-01-31", 2, "2026-03-31"),
        ("2028-01-31", 1, "2028-02-29"),
        ("2026-12-15", 1, "2027-01-15"),
    ],
)
def test_month_end_is_anchored(first, offset, expected):
    assert due_date(date.fromisoformat(first), offset).isoformat() == expected


@pytest.mark.parametrize("value", ["0.00", "-1.00", "NaN", "Infinity", "1.001", 1.1, True])
def test_money_rejects_invalid_or_float(value):
    with pytest.raises(ValidationError):
        Facts.model_validate({**FACTS, "principal": value})


def test_two_layouts_produce_identical_facts():
    second = """TRILHA CCB V2
ref=DEMO-001
amount=1200.00
payment=400.00
count=3
first_due=2026-01-31
"""
    assert extract(TEXT) == extract(second) == (FACTS, [])


@pytest.mark.parametrize(
    "text",
    [
        "scanned image",
        TEXT + "Contrato: DEMO-002",
        TEXT.replace("Principal: 1200.00", "Principal: 1,200.00"),
        TEXT + "Pessoa: should-not-be-retained",
        TEXT.replace("Contrato: DEMO-001\n", ""),
    ],
)
def test_ambiguous_invalid_or_unknown_requires_review(text):
    facts, issues = extract(text)
    assert facts is None and issues
    assert "should-not-be-retained" not in str(issues)


@pytest.mark.parametrize(
    ("amount", "state", "difference"),
    [
        ("400.00", "matched", "0.00"),
        ("200.00", "underpaid", "-200.00"),
        ("401.00", "overpaid", "1.00"),
    ],
)
def test_reconciliation_classifies_exact_money(amount, state, difference):
    result = reconcile(Facts.model_validate(FACTS), [payment(amount)], date(2026, 2, 2))
    assert result["installments"][0]["state"] == state
    assert result["installments"][0]["difference"] == difference


def test_future_payment_is_excluded_from_as_of_snapshot():
    result = reconcile(Facts.model_validate(FACTS), [payment(day="2026-02-03")], date(2026, 2, 2))
    assert result["received_total"] == "0.00"
    assert result["installments"][0]["overdue"] is True


def test_partial_payments_sum_without_float_error():
    payments = [payment("0.10", ref="DEMO-A"), payment("0.20", ref="DEMO-B")]
    result = reconcile(Facts.model_validate(FACTS), payments, date(2026, 2, 2))
    assert result["received_total"] == "0.30"


def test_duplicate_payment_reference_is_invalid():
    with pytest.raises(ValidationError):
        ReconcileInput(as_of="2026-02-02", settlements=[payment(), payment()])


def test_reference_oracle_uses_independent_integer_cents():
    # Generated but deterministic inputs; expected values use integers, never production Decimal.
    for cents in range(1, 1000, 17):
        value = f"{cents // 100}.{cents % 100:02d}"
        result = reconcile(Facts.model_validate(FACTS), [payment(value)], date(2026, 2, 2))
        assert Decimal(result["installments"][0]["difference"]) * 100 == cents - 40000


@pytest.mark.parametrize("content", [b"not a pdf", b"%PDF-invalid", b"x" * 1048577])
def test_pdf_invalid_or_oversize_rejected(content):
    with pytest.raises(ValueError):
        pdf_text(content)
