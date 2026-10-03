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

// --- 2) Tab-switch / away-from-exam + FULLSCREEN detection ---------------
(function () {
  const root = document.getElementById('exam-root');
  if (!root) return;

  function csrfToken() {
    // CSRF_COOKIE_HTTPONLY prevents reading the cookie. Read Django's
    // rendered token from the current root, including after question swaps.
    return liveRoot()?.dataset.csrfToken || '';
  }

  const tabUrl = root.dataset.tabUrl;
  const lockedUrl = root.dataset.lockedUrl;
  const examUrl = root.dataset.examUrl;

  function liveRoot() {
    return document.getElementById('exam-root');
  }

  let reporting = false;
  let lastReportAt = 0;
  let suppressFocusEvents = false;
  let suppressFullscreenEvents = false;
  let navigationTimer;
  let focusLossObserved = false;
  let pendingWarning = null;

  function alertPendingWarning() {
    if (!pendingWarning || suppressFocusEvents || !liveRoot()) return;
    if (document.visibilityState === 'hidden' ||
        (typeof document.hasFocus === 'function' && !document.hasFocus())) return;
    const message = pendingWarning;
    pendingWarning = null;
    // Our own dialog can generate focus events; don't count those as leaving.
    suppressFocusEvents = true;
    try {
      alert(message);
    } finally {
      setTimeout(() => { suppressFocusEvents = false; }, 400);
    }
  }

  function showFocusWarning(message) {
    let notice = document.getElementById('exam-focus-warning');
    if (!notice) {
      notice = document.createElement('div');
      notice.id = 'exam-focus-warning';
      notice.setAttribute('role', 'alert');
      notice.style.cssText = 'position:fixed;top:12px;left:12px;right:12px;z-index:99999;padding:16px;border:2px solid #fbbf24;border-radius:12px;background:#291d05;color:#fff;box-shadow:0 4px 20px #0008;';
      document.body.appendChild(notice);
    }
    notice.replaceChildren();
    const text = document.createElement('p');
    text.textContent = message;
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.textContent = 'Understood';
    dismiss.style.cssText = 'margin-top:8px;padding:6px 12px;border:1px solid #fbbf24;border-radius:6px;';
    dismiss.addEventListener('click', () => notice.remove());
    notice.appendChild(text);
    notice.appendChild(dismiss);
  }

  function markIntentionalNav() {
    // In-place question changes may exit fullscreen, but must not give
    // students a three-second window to switch apps without being counted.
    suppressFullscreenEvents = true;
    clearTimeout(navigationTimer);
    navigationTimer = setTimeout(() => { suppressFullscreenEvents = false; }, 3000);
  }
  window.__examMarkIntentionalNav = markIntentionalNav;
  document.addEventListener('submit', markIntentionalNav, true);
  // pointerdown fires before the browser drops fullscreen on a Submit click,
  // so the following fullscreenchange is treated as navigation, not a violation.
  ['submit-btn', 'skip-btn'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('pointerdown', markIntentionalNav, true);
  });
  const finalForm = document.getElementById('final-submit-form');
  if (finalForm) finalForm.addEventListener('pointerdown', markIntentionalNav, true);

  function reportViolation(type) {
    if (suppressFocusEvents) return;
    const now = Date.now();
    if (reporting || now - lastReportAt < 1500) return;
    reporting = true;
    lastReportAt = now;

    fetch(tabUrl, {
      method: 'POST',
      credentials: 'same-origin',
      keepalive: true,
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken(),
      },
      body: JSON.stringify({ type }),
    })
      .then(res => {
        if (!res.ok) throw new Error(`Report rejected (${res.status})`);
        return res.json();
      })
      .then(data => {
        reporting = false;
        const n = data.attempts;
        const max = data.max || 10;

        if (data.closed) {
          suppressFocusEvents = true;
          alert(`Exam closed: you reached ${n}/${max} focus / fullscreen violations. Your exam has been submitted as-is.`);
          window.location.href = examUrl;
        } else if (data.locked) {
          suppressFocusEvents = true;
          alert(`Locked (attempt ${n}/${max}): leaving the exam screen or exiting fullscreen is not allowed. Locked for ${data.lock_seconds}s.`);
          window.location.href = lockedUrl;
        } else {
          const message = `Warning ${n}/${max}: switching apps, leaving this window, or exiting fullscreen is being logged.`;
          showFocusWarning(message);
          // Queue background warnings until the exam is visible and focused,
          // when the browser can actually present its popup.
          pendingWarning = message;
          alertPendingWarning();
        }
      })
      .catch(() => {
        reporting = false;
        const message = 'The focus violation could not be recorded. Refresh the exam page and tell your teacher if this warning continues.';
        showFocusWarning(message);
        pendingWarning = message;
        alertPendingWarning();
      });
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') reportViolation('tab-switch');
    else alertPendingWarning();
  });
  window.addEventListener('blur', () => {
    if (suppressFocusEvents) return;
    focusLossObserved = true;
    reportViolation('window-blur');
  });
  window.addEventListener('focus', () => {
    focusLossObserved = false;
    alertPendingWarning();
  });

  // Some mobile browsers update focus state without delivering window.blur.
  // Report once per observed loss; covering the page is not itself proof
  // of a loss if the browser continues reporting hasFocus() === true.
  setInterval(() => {
    if (!liveRoot() || suppressFocusEvents || typeof document.hasFocus !== 'function') return;
    const unfocused = document.visibilityState === 'hidden' || !document.hasFocus();
    if (!unfocused) {
      focusLossObserved = false;
      alertPendingWarning();
    } else if (!focusLossObserved) {
      focusLossObserved = true;
      reportViolation('window-blur');
    }
  }, 500);

  // ---------- Fullscreen: report exits only; never auto-re-enter ----------
  // requestFullscreen() is allowed ONLY from the "Return to Fullscreen"
  // button (and login's Start Exam button, which lives in login.html).
  // Calling it while already in fullscreen toggles OUT in Chrome/Edge —
  // that is what made Submit look like it was flipping fullscreen.
  function isFullscreen() {
    return !!(document.fullscreenElement || document.webkitFullscreenElement);
  }
  window.__examCanInteract = isFullscreen;
  // Includes Final Submit as well as the ordinary answer form.
  document.addEventListener('submit', (event) => {
    if (liveRoot() && !isFullscreen()) {
      event.preventDefault();
      event.stopImmediatePropagation();
      setBannerVisible(true);
    }
  }, true);

  let allowEnterFullscreen = false;
  async function enterFullscreen() {
    if (!allowEnterFullscreen) return;
    allowEnterFullscreen = false;
    if (isFullscreen()) return;
    const el = document.documentElement;
    try {
      if (el.requestFullscreen) {
        await el.requestFullscreen({ navigationUI: 'hide' });
      } else if (el.webkitRequestFullscreen) {
        el.webkitRequestFullscreen();
      } else {
        throw new Error('Fullscreen unavailable');
      }
    } catch (e) {
        fsBanner.querySelector('p').textContent = 'Fullscreen was not allowed. Allow fullscreen to continue, or use another browser or device.';
    }
  }

  const fsBanner = document.createElement('div');
  fsBanner.id = 'fs-return-banner';
  fsBanner.className = 'exam-fullscreen-banner';
  fsBanner.setAttribute('role', 'dialog');
  fsBanner.setAttribute('aria-modal', 'true');
  fsBanner.setAttribute('aria-label', 'Fullscreen required');
  fsBanner.style.cssText = 'position:fixed;inset:0;z-index:99998;margin:0;border-radius:0;background:#07110e;align-items:center;justify-content:center;flex-direction:column;padding:24px;text-align:center;';
  fsBanner.innerHTML =
    '<p class="text-sm text-examtext/80">Fullscreen is required. Questions and answering are blocked until you return to fullscreen. Your exam timer continues running.</p>' +
    '<button type="button" id="fs-return-btn" class="shrink-0 rounded-lg border border-examaccent/50 bg-examsurface text-examaccent text-xs font-semibold px-3 py-1.5">Return to Fullscreen</button>';
  function setBannerVisible(show) {
    fsBanner.style.display = show ? 'flex' : 'none';
    const host = liveRoot();
    if (host) {
      host.inert = show;
      host.style.visibility = show ? 'hidden' : '';
    }
    if (show) {
      fsBanner.classList.add('flex');
      fsBanner.classList.remove('hidden');
    } else {
      fsBanner.classList.add('hidden');
      fsBanner.classList.remove('flex');
    }
  }
  function attachFsBanner() {
    const host = liveRoot();
    if (!host) return;
    if (!document.body.contains(fsBanner)) {
      document.body.appendChild(fsBanner);
    }
    const btn = document.getElementById('fs-return-btn');
    if (btn && !btn.dataset.bound) {
      btn.dataset.bound = '1';
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        allowEnterFullscreen = true;
        enterFullscreen();
      });
    }
    setBannerVisible(!isFullscreen());
  }
  attachFsBanner();
  window.__examOnPageSwap = attachFsBanner;

  function onFullscreenChange() {
    const inFs = isFullscreen();
    setBannerVisible(!inFs);
    if (inFs || suppressFocusEvents || suppressFullscreenEvents) return;
    reportViolation('fullscreen-exit');
  }
  document.addEventListener('fullscreenchange', onFullscreenChange);
  document.addEventListener('webkitfullscreenchange', onFullscreenChange);
})();

// --- 3) Countdown display + server resync + in-place submit (keep fullscreen)
(function () {
  let remaining = 0;
  let total = 0;
  let navigating = false;
  const timers = [];

  function clearTimers() {
    while (timers.length) clearInterval(timers.pop());
  }

  function rootEl() {
    return document.getElementById('exam-root');
  }

  async function applyExamHtml(html, url) {
    const next = new DOMParser().parseFromString(html, 'text/html');
    const newRoot = next.getElementById('exam-root');
    if (!newRoot) {
      window.location.assign(url || window.location.href);
      return;
    }
    document.title = next.title;
    if (next.body && next.body.className) document.body.className = next.body.className;
    const oldRoot = rootEl();
    if (oldRoot) oldRoot.replaceWith(newRoot);
    if (url) history.replaceState(null, '', url);
    if (window.__examOnPageSwap) window.__examOnPageSwap();
    initExamForm();
    if (window.__examInitGame) window.__examInitGame();
    navigating = false;
    if (window.ExamUI) window.ExamUI.hideLoading();
  }

  async function swapToUrl(url) {
    if (window.__examMarkIntentionalNav) window.__examMarkIntentionalNav();
    navigating = true;
    try {
      const res = await fetch(url, { credentials: 'same-origin' });
      const html = await res.text();
      await applyExamHtml(html, res.url);
    } catch (e) {
      window.location.assign(url);
    }
  }
  window.__examSwapToUrl = swapToUrl;

  async function postAndStayFullscreen(form) {
    if (navigating) return;
    if (window.__examCanInteract && !window.__examCanInteract()) return;
    if (window.__examMarkIntentionalNav) window.__examMarkIntentionalNav();
    if (window.ExamUI) window.ExamUI.showLoading('Saving…');
    navigating = true;
    // form.action is the hidden <input name="action">, not the POST URL.
    const postUrl = form.getAttribute('action');
    try {
      const res = await fetch(postUrl, {
        method: 'POST',
        body: new FormData(form),
        credentials: 'same-origin',
        redirect: 'follow',
      });
      const html = await res.text();
      await applyExamHtml(html, res.url);
    } catch (e) {
      HTMLFormElement.prototype.submit.call(form);
    }
  }

  function renderTimer() {
    const timerText = document.getElementById('timer-text');
    const timerBar = document.getElementById('timer-bar');
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

  function initExamForm() {
    const root = rootEl();
    if (!root) return;

    clearTimers();
    remaining = parseInt(root.dataset.remaining, 10);
    total = parseInt(root.dataset.total, 10) || remaining;
    navigating = false;
    renderTimer();

    const statusUrl = root.dataset.statusUrl;
    const lockedUrl = root.dataset.lockedUrl;
    const examUrl = root.dataset.examUrl;
    const reviewUrl = root.dataset.reviewUrl;
    const isReview = root.dataset.review === '1';
    const form = document.getElementById('answer-form');
    const actionField = document.getElementById('action-field');
    const skipBtn = document.getElementById('skip-btn');
    let syncing = false;

    timers.push(setInterval(() => {
      if (remaining > 0) {
        remaining -= 1;
        renderTimer();
      }
      // Confirm expiration with the server immediately at zero.
      if (remaining <= 0) syncStatus();
    }, 1000));

    async function syncStatus() {
      if (navigating || syncing || rootEl() !== root) return;
      syncing = true;
      try {
        const res = await fetch(statusUrl, { credentials: 'same-origin' });
        if (!res.ok) return;
        const data = await res.json();

        // Ignore a heartbeat for a question that is being submitted or replaced.
        if (navigating || rootEl() !== root) return;

        if (data.locked) {
          navigating = true;
          if (window.__examMarkIntentionalNav) window.__examMarkIntentionalNav();
          window.location.href = lockedUrl;
          return;
        }
        if (data.closed) {
          navigating = true;
          if (window.__examMarkIntentionalNav) window.__examMarkIntentionalNav();
          window.location.href = examUrl;
          return;
        }
        if (!isReview && data.phase === 'review') {
          navigating = true;
          swapToUrl(reviewUrl);
          return;
        }
        if (typeof data.remaining_seconds === 'number') {
          remaining = data.remaining_seconds;
          renderTimer();
        }
        if (data.expired) {
          navigating = true;
          swapToUrl(isReview ? reviewUrl : examUrl);
        }
      } catch (e) { /* transient network hiccup — next poll will retry */ }
      finally { syncing = false; }
    }
    timers.push(setInterval(syncStatus, 4000));
    if (remaining <= 0) syncStatus();

    if (skipBtn && form && actionField) {
      skipBtn.addEventListener('click', () => {
        actionField.value = 'skip';
        form.querySelectorAll('[required]').forEach(el => el.removeAttribute('required'));
        postAndStayFullscreen(form);
      });
    }
    if (form && actionField) {
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        actionField.value = actionField.value || 'submit';
        postAndStayFullscreen(form);
      });
    }
  }

  initExamForm();
})();

// --- 4) Log-only violation reporting: copy/paste details + idle -----------
// These do NOT affect the tab-switch lock/close schedule (block 2) — they're
// recorded to the Violation audit table for teacher visibility only.
(function () {
  const root = document.getElementById('exam-root');
  if (!root) return;

  const reportUrl = '/report-violation/';
  function logViolation(type, extra) {
    fetch(reportUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': document.getElementById('exam-root')?.dataset.csrfToken || '',
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

  function liveForm() {
    return document.getElementById('answer-form');
  }
  if (!liveForm()) return;

  function submitAnswer() {
    const submitBtn = document.getElementById('submit-btn');
    if (submitBtn) submitBtn.click();
  }
  function skipForLater() {
    const skipBtn = document.getElementById('skip-btn');
    if (skipBtn) skipBtn.click();
  }
  function handleChoiceSelection(key) {
    const form = liveForm();
    if (!form) return;
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
    if (window.__examCanInteract && !window.__examCanInteract()) return;
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
