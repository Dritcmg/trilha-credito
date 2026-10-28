"""Two explicit synthetic layouts; ambiguity always goes to human review."""

import io
import re

from pydantic import ValidationError
from pypdf import PdfReader

from trilha.domain import Facts

MAX_DOCUMENT_BYTES = 1_048_576
MAX_TEXT = 16_000
LABELS = {
    "TRILHA CCB V1": {
        "Contrato": "contract_ref",
        "Principal": "principal",
        "Valor da parcela": "installment_amount",
        "Parcelas": "installments",
        "Primeiro vencimento": "first_due_on",
    },
    "TRILHA CCB V2": {
        "ref": "contract_ref",
        "amount": "principal",
        "payment": "installment_amount",
        "count": "installments",
        "first_due": "first_due_on",
    },
}


def pdf_text(content: bytes) -> str:
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("document_too_large")
    if not content.startswith(b"%PDF-"):
        raise ValueError("invalid_pdf")
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted or len(reader.pages) > 5:
            raise ValueError("unsupported_pdf")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if len(text) > MAX_TEXT:
            raise ValueError("extracted_text_too_large")
        return text
    except ValueError as exc:
        if str(exc) in {"unsupported_pdf", "extracted_text_too_large"}:
            raise
        raise ValueError("unreadable_pdf") from None
    except Exception as exc:
        raise ValueError("unreadable_pdf") from exc


def extract(text: str) -> tuple[dict | None, list[str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] not in LABELS:
        return None, ["unknown_layout_or_image_only_pdf"]
    layout = lines[0]
    separator = ":" if layout.endswith("V1") else "="
    fields = {}
    issues = []
    for line in lines[1:]:
        pair = line.split(separator, 1)
        if len(pair) != 2 or pair[0].strip() not in LABELS[layout]:
            issues.append("unexpected_field")
            continue
        key = LABELS[layout][pair[0].strip()]
        if key in fields:
            issues.append("duplicate_field")
        fields[key] = pair[1].strip()
    count = fields.get("installments", "")
    if re.fullmatch(r"[1-9]\d{0,2}", count):
        fields["installments"] = int(count)
    try:
        facts = Facts.model_validate(fields)
    except ValidationError as exc:
        # Do not persist Pydantic input values, document text or raw exceptions.
        issues.extend(
            "invalid_" + (str(error["loc"][0]) if error["loc"] else "schedule")
            for error in exc.errors()
        )
        return None, sorted(set(issues))
    if issues:
        return None, sorted(set(issues))
    return facts.model_dump(mode="json"), []
