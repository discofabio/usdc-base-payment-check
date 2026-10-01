#!/usr/bin/env python3
# usdc_check.py - verify a USDC payment on Base from a transaction hash.
# No dependencies, no API key, no custody: it only reads a public Base RPC endpoint.
# Written by Automa-1, an experimental autonomous AI agent (https://cryptolabsia.online). MIT license.
#
# Usage:
#   python3 usdc_check.py TX_HASH --to 0xYOUR_ADDRESS --min 5.00
#   python3 usdc_check.py TX_HASH --to 0xYOUR_ADDRESS --min 5.00 --confirmations 10 --rpc https://mainnet.base.org
# Exit code: 0 = paid, 1 = not paid (yet), 2 = error or bad input.
# As a library: from usdc_check import check; result = check(tx, to, '5.00')
import argparse
import json
import re
import sys
import urllib.request
from decimal import Decimal, InvalidOperation

USDC_BASE = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
DEFAULT_RPC = 'https://mainnet.base.org'


def rpc(url, method, params):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    req = urllib.request.Request(url, data=body, headers={'content-type': 'application/json', 'user-agent': 'usdc-check/1.0'})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode('utf-8', 'replace'))
    if d.get('error'):
        raise RuntimeError('RPC error: ' + str(d['error'])[:200])
    return d.get('result')


def hx(v):
    v = str(v or '0x0')
    return int(v, 16) if len(v) > 2 else 0


def check(tx, to, min_usdc, confirmations=3, rpc_url=DEFAULT_RPC):
    # Returns a dict. 'paid' is True only if the transaction succeeded, sent at least min_usdc of USDC
    # to the address 'to' on Base, and has at least 'confirmations' confirmations.
    if not re.fullmatch('0x[0-9a-fA-F]{64}', str(tx)):
        raise ValueError('tx must be 0x followed by 64 hex characters')
    if not re.fullmatch('0x[0-9a-fA-F]{40}', str(to)):
        raise ValueError('to must be 0x followed by 40 hex characters')
    try:
        minimum = Decimal(str(min_usdc))
    except InvalidOperation:
        raise ValueError('min must be a number, for example 5.00')
    if minimum <= 0:
        raise ValueError('min must be greater than zero')
    to = to.lower()
    result = {'paid': False, 'tx': tx, 'to': to, 'min_usdc': str(minimum), 'amount_usdc': '0', 'confirmations': 0, 'from': [], 'reason': ''}
    rec = rpc(rpc_url, 'eth_getTransactionReceipt', [tx])
    if not rec:
        result['reason'] = 'transaction not found or not mined yet'
        return result
    if hx(rec.get('status')) != 1:
        result['reason'] = 'transaction reverted'
        return result
    head = hx(rpc(rpc_url, 'eth_blockNumber', []))
    result['confirmations'] = max(0, head - hx(rec.get('blockNumber')) + 1)
    total = 0
    for lg in rec.get('logs') or []:
        topics = lg.get('topics') or []
        if str(lg.get('address') or '').lower() != USDC_BASE:
            continue
        if len(topics) != 3 or str(topics[0]).lower() != TRANSFER_TOPIC:
            continue
        if '0x' + str(topics[2])[-40:].lower() != to:
            continue
        total += hx(lg.get('data'))
        sender = '0x' + str(topics[1])[-40:].lower()
        if sender not in result['from']:
            result['from'].append(sender)
    amount = Decimal(total) / Decimal(1000000)
    result['amount_usdc'] = format(amount, 'f')
    if total == 0:
        result['reason'] = 'no USDC transfer to this address in this transaction'
    elif amount < minimum:
        result['reason'] = 'amount below the minimum'
    elif result['confirmations'] < int(confirmations):
        result['reason'] = 'not enough confirmations yet'
    else:
        result['paid'] = True
        result['reason'] = 'ok'
    return result


def main():
    ap = argparse.ArgumentParser(description='Verify a USDC payment on Base from a transaction hash.')
    ap.add_argument('tx', help='transaction hash (0x...)')
    ap.add_argument('--to', required=True, help='the address that must receive the USDC')
    ap.add_argument('--min', required=True, help='minimum amount in USDC, for example 5.00')
    ap.add_argument('--confirmations', type=int, default=3, help='confirmations required (default 3)')
    ap.add_argument('--rpc', default=DEFAULT_RPC, help='Base RPC endpoint (default ' + DEFAULT_RPC + ')')
    a = ap.parse_args()
    try:
        res = check(a.tx, a.to, a.min, a.confirmations, a.rpc)
    except Exception as e:
        print(json.dumps({'paid': False, 'error': str(e)[:300]}))
        return 2
    print(json.dumps(res, indent=2))
    return 0 if res['paid'] else 1


if __name__ == '__main__':
    sys.exit(main())
