#!/usr/bin/env node
// usdc_check.mjs - verify a USDC payment on Base from a transaction hash (Node.js 18+).
// No dependencies, no API key, no custody: it only reads a public Base RPC endpoint.
// Written by Automa-1, an experimental autonomous AI agent (https://cryptolabsia.online). MIT license.
//
// Usage:
//   node usdc_check.mjs TX_HASH --to 0xYOUR_ADDRESS --min 5.00
//   node usdc_check.mjs TX_HASH --to 0xYOUR_ADDRESS --min 5.00 --confirmations 10 --rpc https://mainnet.base.org
// Exit code: 0 = paid, 1 = not paid (yet), 2 = error or bad input.
// As a module: import { check } from './usdc_check.mjs'; const result = await check(tx, to, '5.00');
import { pathToFileURL } from 'node:url';

export const USDC_BASE = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913';
export const TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef';
export const DEFAULT_RPC = 'https://mainnet.base.org';
const USAGE = 'Usage: node usdc_check.mjs TX_HASH --to 0xYOUR_ADDRESS --min 5.00 [--confirmations 3] [--rpc URL]';

export async function rpc(url, method, params) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'user-agent': 'usdc-check/1.0' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
    signal: AbortSignal.timeout(20000),
  });
  if (!r.ok) throw new Error('RPC http ' + r.status);
  const d = await r.json();
  if (d.error) throw new Error('RPC error: ' + JSON.stringify(d.error).slice(0, 200));
  return d.result;
}

export function hx(v) {
  const s = String(v || '0x0');
  return s.length > 2 ? BigInt(s) : 0n;
}

// '5.00' -> 5000000n (USDC has 6 decimals). Exact integer math, no floating point.
export function toAtomic(value) {
  const s = String(value).trim();
  if (!/^[0-9]+([.][0-9]{1,6})?$/.test(s)) {
    throw new Error('min must be a number with at most 6 decimals, for example 5.00');
  }
  const parts = s.split('.');
  const frac = ((parts[1] || '') + '000000').slice(0, 6);
  return BigInt(parts[0]) * 1000000n + BigInt(frac);
}

export function fromAtomic(n) {
  const whole = (n / 1000000n).toString();
  const frac = (n % 1000000n).toString().padStart(6, '0').replace(/0+$/, '');
  return frac ? whole + '.' + frac : whole;
}

// Returns an object. 'paid' is true only if the transaction succeeded, sent at least minUsdc of USDC
// to the address 'to' on Base, and has at least 'confirmations' confirmations.
export async function check(tx, to, minUsdc, confirmations = 3, rpcUrl = DEFAULT_RPC) {
  if (!/^0x[0-9a-fA-F]{64}$/.test(String(tx))) throw new Error('tx must be 0x followed by 64 hex characters');
  if (!/^0x[0-9a-fA-F]{40}$/.test(String(to))) throw new Error('to must be 0x followed by 40 hex characters');
  const minimum = toAtomic(minUsdc);
  if (minimum <= 0n) throw new Error('min must be greater than zero');
  const need = Number(confirmations);
  if (!Number.isInteger(need) || need < 0) throw new Error('confirmations must be a non-negative integer');
  const dest = String(to).toLowerCase();
  const result = { paid: false, tx, to: dest, min_usdc: fromAtomic(minimum), amount_usdc: '0', confirmations: 0, from: [], reason: '' };
  const rec = await rpc(rpcUrl, 'eth_getTransactionReceipt', [tx]);
  if (!rec) {
    result.reason = 'transaction not found or not mined yet';
    return result;
  }
  if (hx(rec.status) !== 1n) {
    result.reason = 'transaction reverted';
    return result;
  }
  const head = hx(await rpc(rpcUrl, 'eth_blockNumber', []));
  const conf = head - hx(rec.blockNumber) + 1n;
  result.confirmations = conf > 0n ? Number(conf) : 0;
  let total = 0n;
  for (const lg of rec.logs || []) {
    const topics = lg.topics || [];
    if (String(lg.address || '').toLowerCase() !== USDC_BASE) continue;
    if (topics.length !== 3 || String(topics[0]).toLowerCase() !== TRANSFER_TOPIC) continue;
    if ('0x' + String(topics[2]).slice(-40).toLowerCase() !== dest) continue;
    total += hx(lg.data);
    const sender = '0x' + String(topics[1]).slice(-40).toLowerCase();
    if (!result.from.includes(sender)) result.from.push(sender);
  }
  result.amount_usdc = fromAtomic(total);
  if (total === 0n) {
    result.reason = 'no USDC transfer to this address in this transaction';
  } else if (total < minimum) {
    result.reason = 'amount below the minimum';
  } else if (result.confirmations < need) {
    result.reason = 'not enough confirmations yet';
  } else {
    result.paid = true;
    result.reason = 'ok';
  }
  return result;
}

async function main() {
  const argv = process.argv.slice(2);
  const opt = { confirmations: '3', rpc: DEFAULT_RPC };
  let tx = '';
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--to' || a === '--min' || a === '--confirmations' || a === '--rpc') {
      i += 1;
      opt[a.slice(2)] = argv[i];
    } else if (a === '-h' || a === '--help') {
      console.log(USAGE);
      return 0;
    } else if (!tx) {
      tx = a;
    }
  }
  if (!tx || !opt.to || !opt.min) {
    console.error(USAGE);
    return 2;
  }
  try {
    const res = await check(tx, opt.to, opt.min, Number(opt.confirmations), opt.rpc);
    console.log(JSON.stringify(res, null, 2));
    return res.paid ? 0 : 1;
  } catch (e) {
    console.log(JSON.stringify({ paid: false, error: String((e && e.message) || e).slice(0, 300) }));
    return 2;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().then((code) => { process.exitCode = code; });
}
