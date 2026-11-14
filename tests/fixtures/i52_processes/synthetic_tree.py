"""Disposable fixture, NEVER started automatically. Requires separate approval."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--role', choices=('root','child'), required=True)
    parser.add_argument('--children', type=int, default=0, choices=range(0,4))
    parser.add_argument('--listen', type=int, default=0)
    parser.add_argument('--exit-early', action='store_true')
    parser.add_argument('--child-crash', action='store_true')
    parser.add_argument('--delay-seconds', type=int, choices=range(0, 6), default=0)
    parser.add_argument('--replace-listener', action='store_true')
    parser.add_argument('--ipv6', action='store_true')
    args=parser.parse_args()
    children=[]
    listener=None
    try:
        if args.role=='root':
            for _ in range(args.children):
                command=[sys.executable, __file__, '--role', 'child']
                if args.child_crash: command.append('--child-crash')
                children.append(subprocess.Popen(command))
        if args.delay_seconds:
            time.sleep(args.delay_seconds)
        if args.role == 'child' and args.child_crash:
            return 17
        if args.listen:
            listener=socket.socket(socket.AF_INET6 if args.ipv6 else socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(('::1' if args.ipv6 else '127.0.0.1', args.listen))
            listener.listen(1)
        print(json.dumps({'role':args.role,'pid':os.getpid(),'children':[p.pid for p in children], 'listener':bool(listener)}), flush=True)
        if args.exit_early: return 0
        if args.replace_listener and listener is not None:
            time.sleep(1)
            listener.close()
            listener=socket.socket(socket.AF_INET6 if args.ipv6 else socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(('::1' if args.ipv6 else '127.0.0.1', args.listen))
            listener.listen(1)
            print(json.dumps({'role':args.role,'listener_replaced':True}), flush=True)
        time.sleep(20)
        return 0
    finally:
        if listener is not None: listener.close()


if __name__=='__main__':
    raise SystemExit(main())
