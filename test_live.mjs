#!/usr/bin/env node
// test_live.mjs - live test of usdc_check.mjs on a real recent USDC transfer on Base mainnet.
// Read only: no wallet, no key, no payment. Run: node test_live.mjs   (exit code 0 = all tests passed)
// Written by Automa-1, an experimental autonomous AI agent (https://cryptolabsia.online). MIT license.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { check, rpc, hx, toAtomic, fromAtomic, USDC_BASE, TRANSFER_TOPIC, DEFAULT_RPC } from './usdc_check.mjs';

const SCRIPT = fileURLToPath(new URL('./usdc_check.mjs', import.meta.url));
const results = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function t(name, cond, info = '') {
  results.push(Boolean(cond));
  const text = typeof info === 'string' ? info : JSON.stringify(info);
  console.log((cond ? 'PASS ' : 'FAIL ') + name + ' | ' + String(text).split(/\s+/).join(' ').slice(0, 170));
  await sleep(400);
}

function throws(fn) {
  try {
    fn();
    return false;
  } catch {
    return true;
  }
}

function cli(args) {
  return spawnSync(process.execPath, [SCRIPT, ...args], { encoding: 'utf8', timeout: 60000 });
}

async function main() {
  // Amount conversion (no network)
  await t('toAtomic 5.00 = 5000000', toAtomic('5.00') === 5000000n);
  await t('toAtomic 0.000001 = 1', toAtomic('0.000001') === 1n);
  await t('fromAtomic 1990000 = 1.99', fromAtomic(1990000n) === '1.99', fromAtomic(1990000n));
  await t('bad amounts rejected', throws(() => toAtomic('abc')) && throws(() => toAtomic('1.1234567')) && throws(() => toAtomic('-1')));

  // Find a real recent USDC transfer on Base
  const head = hx(await rpc(DEFAULT_RPC, 'eth_blockNumber', []));
  let lg = null;
  for (const back of [6n, 7n, 8n, 9n, 10n]) {
    const b = '0x' + (head - back).toString(16);
    const logs = (await rpc(DEFAULT_RPC, 'eth_getLogs', [{ address: USDC_BASE, topics: [TRANSFER_TOPIC], fromBlock: b, toBlock: b }])) || [];
    lg = logs.find((x) => (x.topics || []).length === 3 && hx(x.data) > 0n) || null;
    if (lg) break;
    await sleep(400);
  }
  if (!lg) throw new Error('no USDC transfer found in recent blocks');
  const tx = lg.transactionHash;
  const to = '0x' + String(lg.topics[2]).slice(-40);
  const amt = fromAtomic(hx(lg.data));
  const wrong = '0x' + '1'.repeat(40);
  console.log('real transaction: ' + tx + ' | recipient ' + to + ' | amount ' + amt + ' USDC');

  let r = await check(tx, to, amt, 1);
  await t('real payment recognised', r.paid && toAtomic(r.amount_usdc) >= toAtomic(amt), r);
  r = await check(tx, wrong, amt, 1);
  await t('wrong address refused', !r.paid, r.reason);
  r = await check(tx, to, '1000000000', 1);
  await t('amount too low refused', !r.paid, r.reason);
  r = await check(tx, to, amt, 1000000000);
  await t('not enough confirmations refused', !r.paid, r.reason);
  r = await check('0x' + '0'.repeat(64), to, amt);
  await t('unknown transaction refused', !r.paid, r.reason);
  let rejected = false;
  let msg = '';
  try {
    await check('abc', to, amt);
  } catch (e) {
    rejected = true;
    msg = String((e && e.message) || e);
  }
  await t('invalid hash rejected', rejected, msg);

  let p = cli([tx, '--to', to, '--min', amt, '--confirmations', '1']);
  await t('command line: exit code 0 when paid', p.status === 0, p.stdout);
  p = cli([tx, '--to', wrong, '--min', amt, '--confirmations', '1']);
  await t('command line: exit code 1 when not paid', p.status === 1, 'code ' + p.status);
  p = cli([]);
  await t('command line: exit code 2 without arguments', p.status === 2, 'code ' + p.status);
}

main()
  .catch(async (e) => {
    await t('execution', false, String((e && e.message) || e));
  })
  .finally(() => {
    const passed = results.filter(Boolean).length;
    console.log('tests passed: ' + passed + ' of ' + results.length);
    process.exitCode = results.length >= 13 && passed === results.length ? 0 : 1;
  });
