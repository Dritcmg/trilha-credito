"""Pure rules. No credit decision, interest pricing, OCR or external service."""

import calendar
import re
from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
DemoRef = Annotated[str, Field(pattern=r"^DEMO-[A-Z0-9-]{1,48}$")]


class Facts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_ref: DemoRef
    principal: Money
    installment_amount: Money
    installments: Annotated[int, Field(strict=True, ge=1, le=120)]
    first_due_on: date

    @model_validator(mode="after")
    def schedule_must_fit_calendar(self):
        try:
            due_date(self.first_due_on, self.installments - 1)
        except (ValueError, OverflowError):
            raise ValueError("schedule_exceeds_supported_calendar") from None
        return self

    @field_validator("principal", "installment_amount", mode="before")
    @classmethod
    def decimal_string(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r"\d{1,12}\.\d{2}", value):
            raise ValueError("money_requires_decimal_string_with_two_places")
        return value


class Settlement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payment_ref: DemoRef
    installment_number: Annotated[int, Field(strict=True, ge=1, le=120)]
    amount: Money
    paid_on: date

    @field_validator("amount", mode="before")
    @classmethod
    def decimal_string(cls, value):
        return Facts.decimal_string(value)


class ReconcileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: date
    settlements: Annotated[list[Settlement], Field(max_length=120)]

    @field_validator("settlements")
    @classmethod
    def unique_references(cls, value):
        refs = [p.payment_ref for p in value]
        if len(refs) != len(set(refs)):
            raise ValueError("duplicate_payment_reference")
        return value


def due_date(first: date, offset: int) -> date:
    """Monthly due dates clamp to month end, anchored on the ORIGINAL day."""
    month = first.month - 1 + offset
    year = first.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(first.day, calendar.monthrange(year, month)[1]))


def reconcile(facts: Facts, settlements: list[Settlement], as_of: date) -> dict:
    amounts = dict.fromkeys(range(1, facts.installments + 1), Decimal("0.00"))
    for payment in settlements:
        if payment.installment_number > facts.installments:
            raise ValueError("installment_out_of_range")
        if payment.paid_on <= as_of:
            amounts[payment.installment_number] += payment.amount
    rows = []
    for number, received in amounts.items():
        expected = facts.installment_amount
        due = due_date(facts.first_due_on, number - 1)
        state = (
            "matched"
            if received == expected
            else "open"
            if received == 0
            else "underpaid"
            if received < expected
            else "overpaid"
        )
        rows.append(
            {
                "number": number,
                "due_on": due.isoformat(),
                "expected": f"{expected:.2f}",
                "received": f"{received:.2f}",
                "difference": f"{received - expected:.2f}",
                "state": state,
                "overdue": due < as_of and received < expected,
            }
        )
    return {
        "as_of": as_of.isoformat(),
        "contract_ref": facts.contract_ref,
        "expected_total": f"{facts.installment_amount * facts.installments:.2f}",
        "received_total": f"{sum(amounts.values()):.2f}",
        "installments": rows,
    }
