"""Integration-plan runner: refuses launch until a reviewed transport exists.

There is deliberately no flag or environment variable to bypass this gate.
"""
import json
import sys

SCENARIOS = ('separate_process', 'ping', 'status', 'capabilities', 'denied_commands',
             'malformed_oversized', 'duplicate_instance', 'timeout', 'disconnect', 'restart')


def main():
    print(json.dumps({'status': 'NOT_RUN', 'reason': 'reviewed bounded transport unavailable',
                      'development_only': True, 'trusted_broker': False,
                      'scenarios_not_run': list(SCENARIOS)}))
    return 2


if __name__ == '__main__':
    sys.exit(main())
