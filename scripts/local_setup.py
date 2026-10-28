"""Generate ignored local secrets. Never prints tokens or overwrites an existing file."""

import hashlib
import json
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env.local"
if target.exists():
    raise SystemExit("Existing .env.local preserved")
tokens = {role: secrets.token_urlsafe(32) for role in ["operator", "reviewer", "reader"]}
credentials = {
    hashlib.sha256(token.encode()).hexdigest(): {
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "role": role,
    }
    for role, token in tokens.items()
}
password = secrets.token_hex(24)
lines = [
    "# Local-only generated secrets; never commit or paste into issues.",
    "DATABASE_URL=sqlite:///artifacts/local.db",
    "TRILHA_DB_PASSWORD=" + password,
    "TRILHA_CREDENTIALS=" + json.dumps(credentials),
]
lines += ["TRILHA_" + role.upper() + "_TOKEN=" + token for role, token in tokens.items()]
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
(root / "artifacts").mkdir(exist_ok=True)
print("Created ignored .env.local. VS Code reads it; keep tokens on this machine.")
