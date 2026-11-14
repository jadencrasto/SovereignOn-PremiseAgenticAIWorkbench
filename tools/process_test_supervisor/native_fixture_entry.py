"""Fixed fixture entry: diagnostic only, never grants supervision authority.

Do not execute without separately approved isolated native verification.
"""
import argparse
import json
from tools.process_test_supervisor.outer_controller import SCENARIOS


def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--scenario', required=True, choices=sorted(SCENARIOS))
    args = parser.parse_args(argv)
    # No reviewed independent handle-acquisition or pre-resume acknowledgement
    # exists. Do not create a controller or any process until it does.
    print(json.dumps({'status': 'EVIDENCE_INCOMPLETE', 'scenario': args.scenario,
                      'reason': 'observer_handoff_unavailable',
                      'controller_claim': None, 'observer_evidence': None}, sort_keys=True), flush=True)
    return 3


if __name__ == '__main__':
    raise SystemExit(main())
