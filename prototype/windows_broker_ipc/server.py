"""Manual entry point; exits until authenticated Windows IPC is implemented."""
import json
import sys


def main() -> int:
    print(json.dumps({'event': 'startup_refused', 'code': 'TRANSPORT_UNAVAILABLE',
                      'reason': 'Authenticated IPC and exclusive ownership are not verified.'}))
    return 2


if __name__ == '__main__':
    sys.exit(main())
