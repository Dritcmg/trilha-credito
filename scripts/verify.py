"""Reproducible gates; reports observed results without embedding local paths/secrets."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres", action="store_true")
    args = parser.parse_args()
    if args.postgres and not os.environ.get("TRILHA_TEST_DATABASE_URL"):
        raise SystemExit("PostgreSQL gate requires TRILHA_TEST_DATABASE_URL")
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    junit = artifacts / "junit.xml"
    steps = [
        ("lint", ["ruff", "check", "trilha", "tests", "scripts", "migrations"]),
        ("format", ["ruff", "format", "--check", "trilha", "tests", "scripts", "migrations"]),
        ("dependencies", ["pip", "check"]),
        (
            "dependency_audit",
            [
                "pip_audit",
                "-r",
                "requirements.txt",
                "--no-deps",
                "--disable-pip",
                "--strict",
                "--format=json",
                "--output",
                str(artifacts / "audit.json"),
            ],
        ),
        (
            "pytest",
            ["pytest", "-q", "--tb=short", "--junitxml", str(junit)]
            + ([] if args.postgres else ["-m", "not postgres"]),
        ),
    ]
    gates = {}
    for name, command in steps:
        result = subprocess.run([sys.executable, "-m", *command], cwd=ROOT, check=False)
        gates[name] = result.returncode == 0
        if result.returncode:
            raise SystemExit(result.returncode)
    xml = ET.parse(junit).getroot()
    cases = xml.findall(".//testcase")
    counts = {
        "passed": sum(not any(c.tag in {"skipped", "failure", "error"} for c in t) for t in cases),
        "failed": len(xml.findall(".//failure")),
        "errors": len(xml.findall(".//error")),
        "skipped": len(xml.findall(".//skipped")),
    }
    if args.postgres and counts["skipped"]:
        raise SystemExit("PostgreSQL verification cannot contain skipped tests")
    paths = []
    for parent in ["trilha", "tests", "scripts", "migrations"]:
        paths += [p for p in (ROOT / parent).rglob("*.py") if "__pycache__" not in p.parts]
    digest = hashlib.sha256()
    for p in sorted(paths):
        digest.update(p.relative_to(ROOT).as_posix().encode() + b"\0" + p.read_bytes())
    evidence = ROOT / "evidence"
    evidence.mkdir(exist_ok=True)
    report = {
        "status": "PASSED",
        "recorded_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "tests": counts,
        "gates": gates,
        "database_scope": "PostgreSQL plus SQLite" if args.postgres else "SQLite and pure rules",
        "postgres_gate": "PASSED" if args.postgres else "NOT_RUN",
        "source_sha256": digest.hexdigest(),
        "assistance": "AI-assisted portfolio project",
        "limits": "Synthetic data; no production credit decisions or external services.",
    }
    (evidence / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    # Remove machine-specific paths/timings from a compact public test-case list.
    (evidence / "cases.json").write_text(
        json.dumps([{"class": t.get("classname"), "name": t.get("name")} for t in cases], indent=2)
        + "\n"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
