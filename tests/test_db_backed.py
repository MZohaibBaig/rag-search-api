"""DB-backed checks, each run in a subprocess against a throwaway database.

Skipped when no Postgres is reachable, unless REQUIRE_DB is set (CI sets it), in which case a
missing database is a failure rather than a silent skip.
"""
import json
import os
import subprocess
import sys

import pytest

from tests import scratch_db

EVAL_THRESHOLDS = {
    "recall_at_5": 0.95,
    "recall_at_3": 0.80,
    "mrr": 0.80,
    "offtopic_abstain": "2/2",
    "false_abstain_rate": 0.0,
    "example1_ottoline_rank_max": 3,
}


def _run(module: str, *args: str) -> subprocess.CompletedProcess:
    reason = scratch_db.unavailable_reason()
    if reason and not os.environ.get("REQUIRE_DB"):
        pytest.skip(reason)
    return subprocess.run([sys.executable, "-m", module, *args], capture_output=True, text=True,
                          cwd=scratch_db.ROOT, timeout=900,
                          env={**os.environ, "PYTHONPATH": str(scratch_db.ROOT)})


def test_schema_upgrade_and_reseed():
    proc = _run("tests.upgrade_check")
    assert proc.returncode == 0, proc.stdout + proc.stderr[-2000:]


def test_retrieval_eval_meets_thresholds():
    proc = _run("tests.eval.run_eval", "--json")
    assert proc.returncode == 0, proc.stderr[-2000:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    t = EVAL_THRESHOLDS
    assert result["recall_at_5"] >= t["recall_at_5"], result["recall_at_5"]
    assert result["recall_at_3"] >= t["recall_at_3"], result["recall_at_3"]
    assert result["mrr"] >= t["mrr"], result["mrr"]
    assert result["abstain_by_kind"]["offtopic"] == t["offtopic_abstain"], result["abstain_by_kind"]
    assert result["false_abstain_rate"] <= t["false_abstain_rate"], result["false_abstain_rate"]
    assert result["example1_ottoline_rank"] is not None
    assert result["example1_ottoline_rank"] <= t["example1_ottoline_rank_max"], result["example1_ottoline_rank"]
