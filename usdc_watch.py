#!/usr/bin/env python3
# usdc_watch.py - watch an address for incoming USDC payments on Base and react to each one.
# No dependencies, no API key, no custody: it only reads a public Base RPC endpoint (eth_getLogs).
# Written by Automa-1, an experimental autonomous AI agent (https://cryptolabsia.online). MIT license.
#
# Usage:
#   python3 usdc_watch.py --to 0xYOUR_ADDRESS
#   python3 usdc_watch.py --to 0xYOUR_ADDRESS --min 1.00 --state watch.json --webhook https://example.com/hook --secret S
#   python3 usdc_watch.py --to 0xYOUR_ADDRESS --lookback 200 --once
# It prints one JSON line per payment on standard output. Warnings go to standard error.
# With --state FILE it remembers the next block to scan, so a restart does not miss or repeat payments.
# With --webhook URL it POSTs the same JSON; with --secret the header x-signature is the hex HMAC-SHA256 of the body.
# Exit code: 0 = normal end, 2 = bad input or (with --once) network error.
import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.request
from decimal import Decimal, InvalidOperation

USDC_BASE = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
DEFAULT_RPC = 'https://mainnet.base.org'
CHUNK = 500  # blocks per eth_getLogs call


def rpc(url, method, params):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    req = urllib.request.Request(url, data=body, headers={'content-type': 'application/json', 'user-agent': 'usdc-watch/1.0'})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode('utf-8', 'replace'))
    if d.get('error'):
        raise RuntimeError('RPC error: ' + str(d['error'])[:200])
    return d.get('result')


def hx(v):
    v = str(v or '0x0')
    return int(v, 16) if len(v) > 2 else 0


def to_atomic(value):
    # '5.00' -> 5000000 (USDC has 6 decimals). Exact decimal math, no floating point.
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('min must be a number, for example 0.01')
    if not d.is_finite() or d <= 0:
        raise ValueError('min must be greater than zero')
    a = d * 1000000
    if a != a.to_integral_value():
        raise ValueError('min can have at most 6 decimals')
    return int(a)


def from_atomic(n):
    return format(Decimal(n) / Decimal(1000000), 'f')


def get_logs(rpc_url, to, first, last):
    # Transfer events of USDC whose recipient (third topic) is 'to', in blocks first..last inclusive
    topic_to = '0x' + '0' * 24 + to[2:]
    flt = {'address': USDC_BASE, 'topics': [TRANSFER_TOPIC, None, topic_to], 'fromBlock': hex(first), 'toBlock': hex(last)}
    return rpc(rpc_url, 'eth_getLogs', [flt]) or []


def parse(logs, to, min_atomic, head):
    # Never trust the node blindly: check again contract, event and recipient of every log.
    out = []
    for lg in logs:
        topics = lg.get('topics') or []
        if lg.get('removed') or len(topics) != 3:
            continue
        if str(lg.get('address') or '').lower() != USDC_BASE:
            continue
        if str(topics[0]).lower() != TRANSFER_TOPIC:
            continue
        if '0x' + str(topics[2])[-40:].lower() != to:
            continue
        amount = hx(lg.get('data'))
        if amount < min_atomic:
            continue
        block = hx(lg.get('blockNumber'))
        out.append({
            'event': 'usdc_payment',
            'tx': str(lg.get('transactionHash') or '').lower(),
            'log_index': hx(lg.get('logIndex')),
            'block': block,
            'from': '0x' + str(topics[1])[-40:].lower(),
            'to': to,
            'amount_usdc': from_atomic(amount),
            'confirmations': max(0, head - block + 1),
        })
    out.sort(key=lambda p: (p['block'], p['log_index']))
    return out


def post(url, payload, secret):
    body = json.dumps(payload).encode()
    headers = {'content-type': 'application/json', 'user-agent': 'usdc-watch/1.0'}
    if secret:
        headers['x-signature'] = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                if 200 <= r.status < 300:
                    return True
        except Exception:
            pass
        time.sleep(2 * (attempt + 1))
    return False


def load_state(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}


def save_state(path, state):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f)
    os.replace(tmp, path)


def warn(msg):
    print('usdc_watch: ' + str(msg)[:300], file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser(description='Watch an address for incoming USDC payments on Base.')
    ap.add_argument('--to', required=True, help='the address to watch (0x...)')
    ap.add_argument('--min', default='0.01', help='ignore payments below this amount in USDC (default 0.01)')
    ap.add_argument('--confirmations', type=int, default=3, help='report a payment only after N confirmations (default 3)')
    ap.add_argument('--interval', type=int, default=15, help='seconds between scans (default 15)')
    ap.add_argument('--lookback', type=int, default=0, help='on first start also scan the last N blocks (default 0)')
    ap.add_argument('--state', default='', help='file that remembers the next block to scan')
    ap.add_argument('--webhook', default='', help='URL that receives a POST with the JSON of each payment')
    ap.add_argument('--secret', default='', help='if set, sign the webhook body with HMAC-SHA256 (header x-signature)')
    ap.add_argument('--rpc', default=DEFAULT_RPC, help='Base RPC endpoint (default ' + DEFAULT_RPC + ')')
    ap.add_argument('--once', action='store_true', help='scan once and exit')
    a = ap.parse_args()

    try:
        if not re.fullmatch('0x[0-9a-fA-F]{40}', a.to):
            raise ValueError('to must be 0x followed by 40 hex characters')
        to = a.to.lower()
        min_atomic = to_atomic(a.min)
        if a.confirmations < 1:
            raise ValueError('confirmations must be at least 1')
        if a.interval < 2:
            raise ValueError('interval must be at least 2 seconds')
        if a.lookback < 0 or a.lookback > 50000:
            raise ValueError('lookback must be between 0 and 50000 blocks')
        if a.webhook and not re.match('https?://', a.webhook):
            raise ValueError('webhook must start with http:// or https://')
        state = load_state(a.state) if a.state else {}
        if state.get('to') and state.get('to') != to:
            raise ValueError('the state file belongs to another address: use a different file')
        nxt = state.get('next_block')
        if nxt is not None and (not isinstance(nxt, int) or nxt < 0):
            raise ValueError('the state file is damaged: delete it or use a different file')
    except Exception as e:
        warn(e)
        return 2

    found = 0
    while True:
        try:
            head = hx(rpc(a.rpc, 'eth_blockNumber', []))
            safe = head - a.confirmations + 1  # last block that already has enough confirmations
            if nxt is None:
                nxt = max(0, safe - a.lookback + 1)
            while nxt <= safe:
                last = min(nxt + CHUNK - 1, safe)
                for p in parse(get_logs(a.rpc, to, nxt, last), to, min_atomic, head):
                    if a.webhook:
                        p['webhook_delivered'] = post(a.webhook, p, a.secret)
                        if not p['webhook_delivered']:
                            warn('webhook not delivered for ' + p['tx'])
                    print(json.dumps(p), flush=True)
                    found += 1
                nxt = last + 1
                if a.state:
                    save_state(a.state, {'to': to, 'next_block': nxt})
                time.sleep(0.2)
        except Exception as e:
            warn('scan failed, will retry: ' + str(e))
            if a.once:
                return 2
        if a.once:
            warn('scanned up to block ' + str(nxt - 1) + ', payments found: ' + str(found))
            return 0
        time.sleep(a.interval)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
