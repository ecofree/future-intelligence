"""Run a clearly labeled offline integrity/scoring demo; not a real forecast."""
import json
from dataclasses import asdict

from .core.commitments import make_commitment, merkle_root_and_proofs, verify_inclusion
from .core.scoring import brier_score, log_loss


def main() -> None:
    payload = {
        "run_kind": "MOCK_OFFLINE_DEMO",
        "question_version_id": "demo-question-v1",
        "model_config_id": "fixture-no-provider-call",
        "probability_ppm": 700000,
        "external_timestamp_status": "NOT_REQUESTED",
    }
    envelope, digest = make_commitment(payload)
    root, proofs = merkle_root_and_proofs([digest])
    output = {
        "warning": "OFFLINE DEMO ONLY: no model call, actual event or external timestamp",
        "commitment": envelope,
        "record_hash": digest,
        "merkle_root": root,
        "proof": asdict(proofs[0]),
        "content_integrity_valid": verify_inclusion(digest, proofs[0], root),
        "external_timestamp_status": "NOT_REQUESTED",
        "simulated_outcome": 1,
        "brier": brier_score(0.7, 1),
        "log_loss": log_loss(0.7, 1),
    }
    print(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
