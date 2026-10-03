const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const script = fs.readFileSync(path.join(__dirname, '../static/js/exam.js'), 'utf8')
  .split('// --- 3)')[1].split('// --- 4)')[0];
const executable = script.slice(script.indexOf('(function ()'));
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup(remaining = 1) {
  const requests = [], intervals = [], formEvents = {};
  const root = { dataset: {
    remaining: String(remaining), total: '60', statusUrl: '/status/',
    examUrl: '/exam/', reviewUrl: '/review/', lockedUrl: '/locked/',
  } };
  const form = {
    getAttribute: () => '/exam/answer/',
    addEventListener: (name, handler) => { formEvents[name] = handler; },
  };
  const elements = { 'exam-root': root, 'answer-form': form, 'action-field': { value: 'submit' } };
  const window = { location: { assign() {} } };
  vm.runInNewContext(executable, {
    document: { getElementById: id => elements[id] || null }, window,
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval() {}, FormData: class {},
    fetch: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })),
  });
  return { requests, intervals, formEvents, window };
}

test('zero countdown checks expiration immediately and avoids overlapping heartbeats', async () => {
  const s = setup();
  s.intervals.find(i => i.ms === 1000).fn();
  assert.equal(s.requests[0].url, '/status/');
  s.intervals.find(i => i.ms === 4000).fn();
  assert.equal(s.requests.length, 1);
  s.requests[0].resolve({ ok: true, json: async () => ({ expired: true, remaining_seconds: 0 }) });
  await flush();
  assert.equal(s.requests[1].url, '/exam/');
});

test('zero display alone cannot skip a question before server expiry', async () => {
  const s = setup(0);
  s.requests[0].resolve({ ok: true, json: async () => ({ expired: false, remaining_seconds: 0 }) });
  await flush();
  assert.equal(s.requests.length, 1);
});

test('late heartbeat cannot override submission, and double clicks submit once', async () => {
  const s = setup();
  s.intervals.find(i => i.ms === 4000).fn();
  s.formEvents.submit({ preventDefault() {} });
  s.formEvents.submit({ preventDefault() {} });
  assert.equal(s.requests.filter(r => r.url === '/exam/answer/').length, 1);
  s.requests[0].resolve({ ok: true, json: async () => ({ expired: true }) });
  await flush();
  assert.equal(s.requests.length, 2);
});
