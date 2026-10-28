"""Capture tested package versions; no absolute paths or credentials in locks."""

import importlib.metadata as metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
pending = [
    "fastapi",
    "pydantic",
    "SQLAlchemy",
    "uvicorn",
    "psycopg",
    "psycopg-binary",
    "alembic",
    "pypdf",
]
found = set()
while pending:
    name = canonicalize_name(pending.pop())
    if name in found:
        continue
    found.add(name)
    for raw in metadata.requires(name) or []:
        requirement = Requirement(raw)
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
            pending.append(requirement.name)
all_packages = {
    canonicalize_name(d.metadata["Name"])
    for d in metadata.distributions()
    if canonicalize_name(d.metadata["Name"]) not in {"pip", "setuptools"}
}
for filename, packages in [("requirements.txt", found), ("requirements-dev.txt", all_packages)]:
    content = "# Exact versions from the tested Python 3.12 environment.\n"
    content += "".join(f"{name}=={metadata.version(name)}\n" for name in sorted(packages))
    (ROOT / filename).write_text(content, encoding="utf-8", newline="\n")
print("Dependency locks written; credentials and paths excluded.")
