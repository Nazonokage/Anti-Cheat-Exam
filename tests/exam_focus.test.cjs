const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Execute the production focus listeners with browser events under our control.
const script = fs.readFileSync(path.join(__dirname, '../static/js/exam.js'), 'utf8')
  .split('// --- 3)')[0];
function setup(onAlert = () => {}) {
  const alerts = [];
  const windowEvents = {}, documentEvents = {}, requests = [], intervals = [], elements = {};
  const makeElement = () => ({
    style: {}, children: [], classList: { add() {}, remove() {} },
    setAttribute() {}, addEventListener() {}, replaceChildren() { this.children = []; },
    contains(el) { return this.children.includes(el); },
    appendChild(el) { this.children.push(el); if (el.id) elements[el.id] = el; },
  });
  const root = {
    style: {},
    dataset: { tabUrl: '/tab-violation/', csrfToken: 'a'.repeat(64) },
    contains: () => false, insertBefore() {},
  };
  const document = {
    cookie: '', visibilityState: 'visible', fullscreenElement: {},
    focused: true, hasFocus() { return this.focused; }, body: makeElement(),
    getElementById: id => id === 'exam-root' ? root : elements[id] || null,
    createElement: makeElement,
    addEventListener: (name, handler) => { documentEvents[name] = handler; },
  };
  const window = { addEventListener: (name, handler) => { windowEvents[name] = handler; } };
  vm.runInNewContext(script, {
    document, window, Date, setTimeout: () => 1, clearTimeout() {},
    alert: message => { alerts.push(message); onAlert(windowEvents); },
    setInterval: fn => { intervals.push(fn); },
    fetch: (url, options) => {
      return new Promise(resolve => { requests.push({ url, ...options, resolve }); });
    },
  });
  return { document, window, windowEvents, documentEvents, requests, intervals, elements, alerts, root };
}

test('window blur sends an escalating attempt even during a question swap', () => {
  const s = setup();
  s.window.__examMarkIntentionalNav();
  s.windowEvents.blur();
  assert.equal(s.requests.length, 1);
  assert.equal(s.requests[0].url, '/tab-violation/');
  assert.equal(JSON.parse(s.requests[0].body).type, 'window-blur');
  assert.equal(s.requests[0].keepalive, true);
  assert.equal(s.requests[0].headers['X-CSRFToken'], 'a'.repeat(64));
});

test('blur, hidden, and fullscreen exit from one departure count once', () => {
  const s = setup();
  s.windowEvents.blur();
  s.document.visibilityState = 'hidden';
  s.documentEvents.visibilitychange();
  s.document.fullscreenElement = null;
  s.documentEvents.fullscreenchange();
  assert.equal(s.requests.length, 1);
});

test('intentional submission fullscreen exit does not count as an attempt', () => {
  const s = setup();
  s.window.__examMarkIntentionalNav();
  s.document.fullscreenElement = null;
  s.documentEvents.fullscreenchange();
  assert.equal(s.requests.length, 0);
});

test('focus-state fallback catches a missed blur event once per departure', () => {
  const s = setup();
  s.intervals[0]();
  assert.equal(s.requests.length, 0);
  s.document.focused = false;
  s.intervals[0]();
  s.intervals[0]();
  assert.equal(s.requests.length, 1);
  assert.equal(JSON.parse(s.requests[0].body).type, 'window-blur');
});

test('warning remains on the page even when the response arrives while hidden', async () => {
  const s = setup();
  s.windowEvents.blur();
  s.document.visibilityState = 'hidden';
  s.requests[0].resolve({ ok: true, json: async () => ({ attempts: 1, max: 10 }) });
  await new Promise(resolve => setImmediate(resolve));
  const warning = s.elements['exam-focus-warning'];
  assert.ok(warning);
  assert.match(warning.children[0].textContent, /Warning 1\/10/);
  assert.equal(s.alerts.length, 0);
  s.document.visibilityState = 'visible';
  s.windowEvents.focus();
  s.documentEvents.visibilitychange();
  assert.equal(s.alerts.length, 1);
  assert.match(s.alerts[0], /Warning 1\/10/);
  assert.equal(s.elements['exam-focus-warning'], warning);
});

test('visible warnings show a popup without counting the popup blur as another attempt', async () => {
  const s = setup(events => events.blur());
  s.windowEvents.blur();
  s.requests[0].resolve({ ok: true, json: async () => ({ attempts: 2, max: 10 }) });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(s.alerts.length, 1);
  assert.match(s.alerts[0], /Warning 2\/10/);
  assert.equal(s.requests.length, 1);
});

test('app-switch popup waits for focus and fallback presents it when focus returns', async () => {
  const s = setup();
  s.document.focused = false;
  s.windowEvents.blur();
  s.requests[0].resolve({ ok: true, json: async () => ({ attempts: 1 }) });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(s.alerts.length, 0);
  s.document.focused = true;
  s.intervals[0]();
  assert.equal(s.alerts.length, 1);
});

test('fullscreen exit hides questions and blocks submissions until fullscreen returns', () => {
  const s = setup();
  s.document.fullscreenElement = null;
  s.documentEvents.fullscreenchange();
  assert.equal(s.root.inert, true);
  assert.equal(s.root.style.visibility, 'hidden');
  let prevented = false;
  s.documentEvents.submit({ preventDefault() { prevented = true; }, stopImmediatePropagation() {} });
  assert.equal(prevented, true);
  assert.equal(s.window.__examCanInteract(), false);
  s.document.fullscreenElement = {};
  s.documentEvents.fullscreenchange();
  assert.equal(s.root.inert, false);
  assert.equal(s.root.style.visibility, '');
  assert.equal(s.window.__examCanInteract(), true);
});

test('report reads the new page token after a question swap', () => {
  const s = setup();
  s.root.dataset.csrfToken = 'b'.repeat(64);
  s.windowEvents.blur();
  assert.equal(s.requests[0].headers['X-CSRFToken'], 'b'.repeat(64));
});

test('rejected reports show an error instead of pretending the attempt was counted', async () => {
  const s = setup();
  s.windowEvents.blur();
  s.requests[0].resolve({ ok: false, status: 403 });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(s.alerts[0], /could not be recorded/);
  assert.doesNotMatch(s.alerts[0], /Warning \d/);
});
