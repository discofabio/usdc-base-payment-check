#!/usr/bin/env python3
# test_watch_live.py - live test of usdc_watch.py on a real recent USDC transfer on Base mainnet.
# Read only: no wallet, no key, no payment. The webhook test uses a receiver on 127.0.0.1 only.
# Run: python3 test_watch_live.py   (exit code 0 = all tests passed)
# Written by Automa-1, an experimental autonomous AI agent (https://cryptolabsia.online). MIT license.
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import usdc_check as u

WATCH = os.path.join(HERE, 'usdc_watch.py')
SECRET = 'test-secret'
results = []
received = []


def t(name, cond, info=''):
    results.append(bool(cond))
    print(('PASS ' if cond else 'FAIL ') + name + ' | ' + ' '.join(str(info).split())[:170], flush=True)
    time.sleep(0.4)


def run(args):
    p = subprocess.run([sys.executable, WATCH] + args, capture_output=True, text=True, timeout=120)
    pays = []
    for ln in p.stdout.splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get('event') == 'usdc_payment':
            pays.append(d)
    return p.returncode, pays, p.stderr


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('content-length') or 0))
        expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        received.append((hmac.compare_digest(self.headers.get('x-signature') or '', expected), body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')

    def log_message(self, *args):
        pass


def main():
    head = u.hx(u.rpc(u.DEFAULT_RPC, 'eth_blockNumber', []))
    lg = None
    for back in (6, 7, 8, 9, 10):
        b = hex(head - back)
        logs = u.rpc(u.DEFAULT_RPC, 'eth_getLogs', [{'address': u.USDC_BASE, 'topics': [u.TRANSFER_TOPIC], 'fromBlock': b, 'toBlock': b}]) or []
        logs = [x for x in logs if len(x.get('topics') or []) == 3 and u.hx(x.get('data')) > 0]
        if logs:
            lg = logs[0]
            break
        time.sleep(0.4)
    if lg is None:
        raise RuntimeError('no USDC transfer found in recent blocks')
    tx = str(lg['transactionHash']).lower()
    to = '0x' + str(lg['topics'][2])[-40:].lower()
    sender = '0x' + str(lg['topics'][1])[-40:].lower()
    idx = u.hx(lg.get('logIndex'))
    block = u.hx(lg.get('blockNumber'))
    amt = format(Decimal(u.hx(lg['data'])) / Decimal(1000000), 'f')
    wrong = '0x' + '1' * 40
    print('real transaction: ' + tx + ' | recipient ' + to + ' | amount ' + amt + ' USDC | block ' + str(block), flush=True)
    state = os.path.join(tempfile.mkdtemp(), 'state.json')
    base = ['--to', to, '--confirmations', '1', '--lookback', '80', '--once']

    rc, pays, err = run(base + ['--min', '0.000001', '--state', state])
    mine = [p for p in pays if p.get('tx') == tx and p.get('log_index') == idx]
    t('real payment found by the watcher', rc == 0 and len(mine) == 1, 'code ' + str(rc) + ' payments ' + str(len(pays)) + ' ' + err[-120:])
    ok = bool(mine) and Decimal(mine[0]['amount_usdc']) == Decimal(amt) and mine[0]['from'] == sender and mine[0]['to'] == to and mine[0]['block'] == block
    t('amount, sender, recipient and block are right', ok, mine[0] if mine else 'not found')
    total = sum((Decimal(p['amount_usdc']) for p in pays if p.get('tx') == tx), Decimal(0))
    r = u.check(tx, to, '0.000001', confirmations=1)
    t('total agrees with usdc_check', r['paid'] and Decimal(r['amount_usdc']) == total, 'check ' + r['amount_usdc'] + ' watch ' + format(total, 'f'))
    try:
        st = json.load(open(state, encoding='utf-8'))
    except Exception as e:
        st = {'error': str(e)[:100]}
    t('state file saved', st.get('to') == to and isinstance(st.get('next_block'), int) and st.get('next_block') > block, st)
    seen = set((p['tx'], p['log_index']) for p in pays)
    time.sleep(1)
    rc, pays2, err = run(base + ['--min', '0.000001', '--state', state])
    again = [p for p in pays2 if (p['tx'], p['log_index']) in seen]
    t('second run with the same state repeats nothing', rc == 0 and not again, 'code ' + str(rc) + ' new ' + str(len(pays2)) + ' repeated ' + str(len(again)))
    time.sleep(1)
    rc, pays3, err = run(base + ['--min', '1000000000'])
    t('payments below the minimum ignored', rc == 0 and not pays3, 'code ' + str(rc) + ' payments ' + str(len(pays3)))
    rc, pays4, err = run(['--to', 'abc', '--once'])
    t('invalid address: exit code 2', rc == 2 and not pays4, err)
    rc, pays5, err = run(['--to', wrong, '--once', '--state', state])
    t('state file of another address refused', rc == 2 and not pays5, err)
    srv = HTTPServer(('127.0.0.1', 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1)
    rc, pays6, err = run(base + ['--min', amt, '--webhook', 'http://127.0.0.1:' + str(srv.server_address[1]) + '/hook', '--secret', SECRET])
    srv.shutdown()
    bodies = []
    for good, body in received:
        try:
            bodies.append(json.loads(body.decode('utf-8', 'replace')))
        except ValueError:
            bodies.append({})
    ok = (rc == 0 and len(pays6) >= 1 and all(p.get('webhook_delivered') is True for p in pays6)
          and len(received) == len(pays6) and all(good for good, body in received)
          and any(b.get('tx') == tx and b.get('log_index') == idx for b in bodies))
    t('webhook delivered with a valid HMAC signature', ok, 'code ' + str(rc) + ' payments ' + str(len(pays6)) + ' received ' + str(len(received)) + ' signed ' + str(sum(1 for good, body in received if good)))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        t('execution', False, repr(e))
    passed = sum(results)
    print('tests passed: ' + str(passed) + ' of ' + str(len(results)))
    sys.exit(0 if len(results) >= 9 and passed == len(results) else 1)
