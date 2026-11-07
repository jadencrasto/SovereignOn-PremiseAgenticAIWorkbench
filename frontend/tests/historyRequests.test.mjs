import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadOwnedHistory, deleteOwnedHistory } from '../src/components/chat/historyRequests.ts';

test('server-authorized histories remain separate on account switch and return', async () => {
  const histories = { a: { a1: 'A secret' }, b: { b1: 'B only' }, local: { l1: 'Local' } };
  const load = async account => loadOwnedHistory(
    async () => ({ sessions: Object.keys(histories[account]) }),
    async id => histories[account][id], () => true,
  );
  assert.deepEqual(await load('a'), ['A secret']);
  assert.deepEqual(await load('b'), ['B only']);
  assert.deepEqual(await load('a'), ['A secret']);
  assert.deepEqual(await load('local'), ['Local']);
});

test('stale account response is discarded after logout or switch', async () => {
  let resolve;
  const pending = new Promise(r => { resolve = r; });
  let generation = 0;
  const old = generation;
  const request = loadOwnedHistory(() => pending, async () => 'old secret', () => old === generation);
  generation++;
  resolve({ sessions: ['a1'] });
  assert.equal(await request, null);
});

test('stale read response is discarded even after list completed', async () => {
  let resolve;
  const pending = new Promise(r => { resolve = r; });
  let current = true;
  const request = loadOwnedHistory(async () => ({ sessions: ['a1'] }), () => pending, () => current);
  await Promise.resolve();
  current = false;
  resolve('old secret');
  assert.equal(await request, null);
});

test('successful delete requires server confirmation; failures do not succeed', async () => {
  let items = ['a1'];
  if (await deleteOwnedHistory('a1', async () => {}, () => true)) items = [];
  assert.deepEqual(items, []);
  items = ['a1'];
  for (const error of [401, 403, 404, 'network']) {
    await assert.rejects(deleteOwnedHistory('a1', async () => { throw new Error(String(error)); }, () => true));
    assert.deepEqual(items, ['a1']);
  }
  assert.equal(await deleteOwnedHistory('a1', async () => {}, () => false), false);
});
