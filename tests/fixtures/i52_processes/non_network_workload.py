"""Fixed synthetic workload; execute only under separately approved isolation."""
import argparse
import json
import os
import sys
import time
import uuid


def cooperate(operation, binding, channel, parent, native, deadline):
    """Called only by the reviewed bootstrap after it pins the root channel."""
    from tools.process_test_supervisor.handoff import CooperativeDescendant, HandoffError

    if binding.op != operation:
        raise HandoffError('Fixture operation mismatch')
    command = [sys.executable, '-I', '-B', os.path.abspath(__file__), '--scenario', 'child',
               '--operation-id', operation]
    fixture = CooperativeDescendant(binding, parent, native, lambda: native.create_suspended_descendant(command))
    fixture.run(channel, deadline)


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--scenario', required=True, choices=('normal', 'stall', 'descendant', 'early_exit', 'reject_containment', 'child'))
    parser.add_argument('--operation-id', required=True)
    args = parser.parse_args()
    if str(uuid.UUID(args.operation_id)) != args.operation_id:
        return 2
    if args.scenario == 'reject_containment':
        return 2  # The controller must not resume this case.
    if args.scenario == 'descendant':
        return 2  # The blocked standalone entry has no authenticated bootstrap.
    print(json.dumps({'operation_id': args.operation_id, 'scenario': args.scenario, 'pid': os.getpid()}), flush=True)
    if args.scenario == 'early_exit':
        return 0
    time.sleep(1 if args.scenario == 'normal' else 60)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
