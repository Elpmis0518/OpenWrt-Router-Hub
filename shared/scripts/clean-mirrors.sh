#!/usr/bin/env bash
# Remove slow China mirrors (.cn/, tencent, aliyun) from OpenWrt projectsmirrors.json.
set -euo pipefail

OPENWRT_DIR="${1:-.}"
MIRRORS_FILE="${OPENWRT_DIR}/scripts/projectsmirrors.json"

if [ ! -f "$MIRRORS_FILE" ]; then
  echo "projectsmirrors.json not found at $MIRRORS_FILE; skipping."
  exit 0
fi

python3 - "$MIRRORS_FILE" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
pattern = re.compile(r"(\.cn/|tencent|aliyun)", re.IGNORECASE)
raw = path.read_text(encoding="utf-8")

try:
    data = json.loads(raw)
except json.JSONDecodeError:
    lines = [ln for ln in raw.splitlines() if not pattern.search(ln)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Filtered {path} via line mode.")
    sys.exit(0)

removed = 0
for key, urls in list(data.items()):
    if isinstance(urls, list):
        kept = [u for u in urls if not (isinstance(u, str) and pattern.search(u))]
        removed += len(urls) - len(kept)
        data[key] = kept

path.write_text(json.dumps(data, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Cleaned {removed} slow mirror entries from {path}.")
PY
