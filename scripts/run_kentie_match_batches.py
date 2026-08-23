from __future__ import annotations

import json
import sys

sys.path.insert(0, "/srv/weldingshop-pim")
sys.path.insert(0, "/srv/weldingshop-pim/scripts")

from kentie_match_batch_sync import run


for batch in range(14, 24):
    try:
        result = run(batch, enrich=True, apply=True)
    except Exception as exc:
        print(json.dumps({"batch": batch, "status": "failed", "error": str(exc)},
                         ensure_ascii=False), flush=True)
        raise
    print(json.dumps({"status": "completed", **result}, ensure_ascii=False), flush=True)
