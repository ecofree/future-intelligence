"""Explicit FIXTURE runner, not an AI forecaster. LIVE experiments are rejected."""
from __future__ import annotations

import argparse
import hashlib
import json
import os

from .repository import TaskRepository
from .spec import uniform_ppm


def run_once(repository: TaskRepository, experiment: str) -> dict | None:
    lease = repository.claim(experiment, "FIXTURE")
    if lease is None:
        return None
    if lease["run_kind"] != "FIXTURE":
        raise RuntimeError("refuse to fabricate a LIVE result")
    if not repository.start_call(lease):
        raise RuntimeError("call already started; reconcile rather than dispatch again")
    spec = lease["question_spec"]
    categories = 2 if spec["question_type"] == "binary" else len(spec["bin_edges"]) + 1
    probabilities = uniform_ppm(categories)
    digest = hashlib.sha256(json.dumps({"FIXTURE": True, "probabilities": probabilities}).encode()).hexdigest()
    receipt = repository.succeed(lease, probabilities, digest, 0)
    return {"run_kind": "FIXTURE", "task_id": lease["task_id"], "receipt_sha256": receipt,
            "warning": "No provider call or external time proof; zero is fixture cost only."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment")
    args = parser.parse_args()
    dsn = os.environ.get("FI_WORKER_DATABASE_URL")
    if not dsn:
        parser.error("FI_WORKER_DATABASE_URL must contain a separately bound worker login")
    print(json.dumps(run_once(TaskRepository(dsn), args.experiment), ensure_ascii=False))


if __name__ == "__main__":
    main()
