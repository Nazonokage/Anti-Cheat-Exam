const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../templates/login.html'), 'utf8')
  .split('<script>')[1].split('</script>')[0];

function setup(requestFullscreen) {
  let submitHandler, submitted = 0;
  const error = { hidden: true };
  const form = { addEventListener: (_, fn) => { submitHandler = fn; } };
  const document = {
    documentElement: {}, fullscreenElement: null,
    getElementById: id => ({ 'login-form': form, 'fullscreen-error': error }[id] || null),
  };
  if (requestFullscreen) document.documentElement.requestFullscreen = () => requestFullscreen(document);
  vm.runInNewContext(script, {
    document, window: {}, HTMLFormElement: { prototype: { submit() { submitted++; } } },
  });
  return { submit: () => submitHandler({ preventDefault() {} }), error, count: () => submitted };
}

test('unsupported fullscreen does not submit login or start the exam', async () => {
  const s = setup();
  await s.submit();
  assert.equal(s.count(), 0);
  assert.equal(s.error.hidden, false);
});

test('denied fullscreen does not submit login', async () => {
  const s = setup(() => Promise.reject(new Error('Denied')));
  await s.submit();
  assert.equal(s.count(), 0);
  assert.equal(s.error.hidden, false);
});

test('login waits for confirmed fullscreen and submits only once', async () => {
  let enter;
  const s = setup(document => new Promise(resolve => {
    enter = () => { document.fullscreenElement = {}; resolve(); };
  }));
  const starting = s.submit();
  await s.submit();
  assert.equal(s.count(), 0);
  enter();
  await starting;
  assert.equal(s.count(), 1);
});
