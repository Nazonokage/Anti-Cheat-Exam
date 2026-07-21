// exam.js — client-side behavior for the one-question exam screen and review screen.
// IMPORTANT: this file only *displays* time; the server is always the source of
// truth for remaining time (see status_api / question_started_at).
//
// STRUCTURE NOTE: the anti-cheat protections (copy/cut/right-click block,
// devtools-shortcut block, tab-switch detection) are registered FIRST, and
// wrapped separately from the timer/form logic below. JavaScript runs
// top-to-bottom and stops on an uncaught error — if these were registered
// last (as in an earlier version of this file), a bug anywhere above them
// would silently kill copy-block and tab detection along with it. Keeping
// them first and isolated means they come up even if something else on the
// page misbehaves.

// --- 1) Copy / right-click / devtools-shortcut deterrents ------------------
// These are deterrents, not guarantees (see plan.md / README) — a determined
// student can still open devtools from the browser's own menu; no page-level
// JS can fully prevent that in modern browsers. This still reliably blocks
// casual copy/paste and right-click.
(function () {
  document.addEventListener('copy', e => e.preventDefault());
  document.addEventListener('cut', e => e.preventDefault());
  document.addEventListener('contextmenu', e => e.preventDefault());
  document.addEventListener('keydown', e => {
    if (e.key === 'F12' || (e.ctrlKey && e.shiftKey && ['I', 'J', 'C'].includes(e.key))) {
      e.preventDefault();
    }
  });
})();

// --- 2) Tab-switch / away-from-exam detection -------------------------------
// Runs on every page that loads this file, independent of exam-specific
// elements existing. Uses BOTH the Page Visibility API and window blur as a
// fallback, since visibilitychange alone can miss some alt-tab / other-app
// scenarios depending on browser/OS. A short cooldown stops the two from
// double-counting the same physical switch as two separate violations.
//
// reportViolation(type) POSTs { type } as JSON to /tab-violation/, and shows
// an alert() with the running count on EVERY attempt (1 through 10) — not
// just once locked — so the student always knows exactly where they stand.
(function () {
  const root = document.getElementById('exam-root');
  if (!root) return; // only report violations while an exam/review is actually in progress

  function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : null;
  }

  const tabUrl = root.dataset.tabUrl;
  const lockedUrl = root.dataset.lockedUrl;
  const examUrl = root.dataset.examUrl;

  let reporting = false;
  let lastReportAt = 0;
  let suppressFocusEvents = false; // true while OUR OWN alert() is open, OR while a form is submitting

  // Submitting the answer form (Submit / Skip / Final Submit) navigates the
  // page. On some mobile browsers, that navigation itself — or the
  // on-screen keyboard closing as part of it — can spuriously fire
  // 'blur'/'visibilitychange' right as the old page unloads, which was
  // getting miscounted as a tab-switch violation immediately after posting
  // an answer. Any real <form> submit on this page (capture phase, so it
  // runs before the browser starts navigating) marks this as intentional.
  function markIntentionalNav() {
    suppressFocusEvents = true;
    // Safety net: if navigation doesn't actually happen (e.g. a required
    // field blocked a submit, or a buff call failed), don't leave
    // tab-switch detection disabled for the rest of the exam.
    setTimeout(() => { suppressFocusEvents = false; }, 3000);
  }
  // Exposed so other scripts on this page (e.g. game.js reloading after a
  // buff action) can mark their own navigation as intentional too.
  window.__examMarkIntentionalNav = markIntentionalNav;

  document.addEventListener('submit', markIntentionalNav, true);

  function reportViolation(type) {
    if (suppressFocusEvents) return; // ignore blur/hidden caused by our own alert() or a form submit
    const now = Date.now();
    if (reporting || now - lastReportAt < 1500) return; // dedupe near-simultaneous blur+hidden
    reporting = true;
    lastReportAt = now;

    fetch(tabUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken'),
      },
      body: JSON.stringify({ type }),
    })
      .then(res => res.json())
      .then(data => {
        reporting = false;
        const n = data.attempts;
        const max = data.max || 10;

        // Native alert() dialogs can themselves fire a 'blur' event on window
        // in some browsers. Suppress our own listeners for the duration (plus
        // a short buffer after) so dismissing the alert doesn't get counted
        // as ANOTHER violation, which would otherwise cascade into stacked
        // alerts if the student takes a moment to read/dismiss one.
        suppressFocusEvents = true;
        if (data.closed) {
          alert(`Exam closed: you reached ${n}/${max} tab-switch / focus-loss attempts. ` +
                `Your exam has been submitted as-is.`);
          window.location.href = examUrl;
        } else if (data.locked) {
          alert(`Locked (attempt ${n}/${max}): tab-switching / losing focus is not allowed. ` +
                `You're locked out for ${data.lock_seconds}s. ` +
                `Reaching ${max} will end your exam automatically.`);
          window.location.href = lockedUrl;
        } else {
          alert(`Warning ${n}/${max}: switching tabs or leaving this window is being logged. ` +
                `Repeated attempts will lock you out, then end your exam.`);
          setTimeout(() => { suppressFocusEvents = false; }, 300);
        }
      })
      .catch(() => { reporting = false; });
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') reportViolation('tab-switch');
  });
  window.addEventListener('blur', () => reportViolation('window-blur'));
})();

// --- 3) Countdown display + server resync + auto-save form wiring ----------
(function () {
  const root = document.getElementById('exam-root');
  if (!root) return; // done/locked screens don't need this

  const statusUrl = root.dataset.statusUrl;
  const lockedUrl = root.dataset.lockedUrl;
  const examUrl = root.dataset.examUrl;
  const reviewUrl = root.dataset.reviewUrl;
  const isReview = root.dataset.review === '1';

  let remaining = parseInt(root.dataset.remaining, 10);
  const total = parseInt(root.dataset.total, 10) || remaining;

  const timerText = document.getElementById('timer-text');
  const timerBar = document.getElementById('timer-bar');
  const form = document.getElementById('answer-form');
  const actionField = document.getElementById('action-field');
  const skipBtn = document.getElementById('skip-btn');
  const submitBtn = document.getElementById('submit-btn');

  let navigating = false;

  function renderTimer() {
    if (timerText) {
      timerText.textContent = Math.max(0, remaining) + 's';
      // remaining CAN exceed `total` (the exam's base seconds_per_question)
      // — that's intentional: the +30s time-boost buff is meant to push
      // past the normal max, not just top it back up. Make that state
      // visibly obvious rather than looking like a rendering glitch.
      timerText.classList.toggle('text-cyan-300', remaining > total);
      timerText.classList.toggle('text-examaccent', remaining <= total);
    }
    if (timerBar && total > 0) {
      const boosted = remaining > total;
      const pct = Math.max(0, Math.min(100, (remaining / total) * 100));
      timerBar.style.width = pct + '%';
      timerBar.classList.toggle('bg-red-400', !boosted && pct < 20);
      timerBar.classList.toggle('bg-examprimary', !boosted && pct >= 20);
      timerBar.classList.toggle('holo-btn', boosted); // shimmering gradient while boosted past max
    }
  }
  renderTimer();

  // Local visual ticking between server syncs.
  setInterval(() => {
    if (remaining > 0) {
      remaining -= 1;
      renderTimer();
    }
  }, 1000);

  // Server resync + heartbeat, every 4s. This is also what powers the live
  // teacher dashboard (last_heartbeat is updated on every hit to status_url).
  async function syncStatus() {
    if (navigating) return;
    try {
      const res = await fetch(statusUrl, { credentials: 'same-origin' });
      if (!res.ok) return;
      const data = await res.json();

      if (data.locked) {
        navigating = true;
        window.location.href = lockedUrl;
        return;
      }
      if (data.closed) {
        navigating = true;
        window.location.href = examUrl;
        return;
      }
      if (!isReview && data.phase === 'review') {
        navigating = true;
        window.location.href = reviewUrl;
        return;
      }
      if (typeof data.remaining_seconds === 'number') {
        remaining = data.remaining_seconds;
        renderTimer();
      }
      if (data.expired) {
        // Server has already auto-skipped/auto-submitted; reload to fetch the next question.
        navigating = true;
        window.location.reload();
      }
    } catch (e) { /* transient network hiccup — next poll will retry */ }
  }
  setInterval(syncStatus, 4000);

  // Skip button.
  if (skipBtn && form && actionField) {
    skipBtn.addEventListener('click', () => {
      actionField.value = 'skip';
      // Skipping a not-required field: temporarily remove "required" so the
      // browser doesn't block submission on an empty choice/text field.
      form.querySelectorAll('[required]').forEach(el => el.removeAttribute('required'));
      form.submit();
    });
  }
  if (submitBtn && form && actionField) {
    form.addEventListener('submit', () => { actionField.value = actionField.value || 'submit'; });
  }
})();

// --- 4) Log-only violation reporting: copy/paste details + idle -----------
// These do NOT affect the tab-switch lock/close schedule (block 2) — they're
// recorded to the Violation audit table for teacher visibility only.
(function () {
  const root = document.getElementById('exam-root');
  if (!root) return;

  function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : null;
  }

  const reportUrl = '/report-violation/';
  function logViolation(type, extra) {
    fetch(reportUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken'),
      },
      body: JSON.stringify(Object.assign({ type }, extra || {})),
    }).catch(() => { /* best-effort logging; ignore network hiccups */ });
  }

  // Copy/cut/contextmenu are already blocked in block 1 — this just logs
  // WHAT was attempted (truncated) so a teacher can review it later.
  document.addEventListener('copy', () => {
    const selected = window.getSelection ? window.getSelection().toString() : '';
    logViolation('copy_attempt', { selected_text: selected.substring(0, 200) });
  });
  document.addEventListener('cut', () => logViolation('cut_attempt'));
  document.addEventListener('paste', () => logViolation('paste_attempt'));

  // Prolonged idle: no mouse/keyboard/touch activity for 30s straight.
  const IDLE_THRESHOLD_SECONDS = 30;
  let idleSeconds = 0;
  let idleAlreadyFlagged = false;
  function resetIdle() { idleSeconds = 0; idleAlreadyFlagged = false; }
  document.addEventListener('mousemove', resetIdle);
  document.addEventListener('keydown', resetIdle);
  document.addEventListener('touchstart', resetIdle);
  setInterval(() => {
    idleSeconds += 1;
    if (idleSeconds >= IDLE_THRESHOLD_SECONDS && !idleAlreadyFlagged) {
      idleAlreadyFlagged = true;
      logViolation('prolonged_idle', { idle_seconds: idleSeconds });
    }
  }, 1000);
})();

// --- 5) Keyboard shortcuts for answering -----------------------------------
// Enter = submit, Ctrl/Cmd+S = skip, number/letter keys = pick a choice.
// Typing in the identification text input is left alone except Enter.
(function () {
  const root = document.getElementById('exam-root');
  if (!root) return;

  const form = document.getElementById('answer-form');
  const submitBtn = document.getElementById('submit-btn');
  const skipBtn = document.getElementById('skip-btn');
  if (!form) return;

  function submitAnswer() {
    if (submitBtn) submitBtn.click();
  }
  function skipForLater() {
    if (skipBtn) skipBtn.click();
  }
  function handleChoiceSelection(key) {
    const choices = form.querySelectorAll('.choice-option');
    if (!choices.length) return;
    const keyLower = key.toLowerCase();
    let index = -1;
    if (/^[1-9]$/.test(key)) {
      index = parseInt(key, 10) - 1;           // 1-based -> 0-based
    } else if (/^[a-z]$/.test(keyLower)) {
      index = keyLower.charCodeAt(0) - 97;     // a=0, b=1, ...
    }
    if (index >= 0 && index < choices.length) {
      const input = choices[index].querySelector('input[type="radio"], input[type="checkbox"]');
      if (input) input.checked = true;
    }
  }

  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        submitAnswer();
      }
      return;
    }

    if (e.key === 'Enter') {
      submitAnswer();
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      skipForLater();
    } else {
      handleChoiceSelection(e.key);
    }
  });
})();
