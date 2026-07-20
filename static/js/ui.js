// ui.js — shared across every page: the pedro.gif loading overlay and a
// tiny synthesized sound-effect helper (no external audio files, so no
// licensing/hosting concerns — just short generated tones via Web Audio).
window.ExamUI = (function () {
  function showLoading(text) {
    const overlay = document.getElementById('exam-loading-overlay');
    if (!overlay) return;
    const label = document.getElementById('loading-text');
    if (label) label.textContent = text || 'Loading…';
    overlay.classList.remove('hidden');
  }

  function hideLoading() {
    const overlay = document.getElementById('exam-loading-overlay');
    if (overlay) overlay.classList.add('hidden');
  }

  // --- Synthesized sound effects --------------------------------------------
  // Browsers block audio until a user gesture has happened on the page, so
  // the very first sound (e.g. a checkpoint toast on page load) may be
  // silent until the student clicks/taps something — that's a browser
  // policy, not a bug here.
  let audioCtx = null;
  function getCtx() {
    if (audioCtx) return audioCtx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    audioCtx = new AC();
    return audioCtx;
  }

  function tone(freq, duration, type, gain, delay) {
    const c = getCtx();
    if (!c) return;
    const start = c.currentTime + (delay || 0);
    const osc = c.createOscillator();
    const g = c.createGain();
    osc.type = type || 'sine';
    osc.frequency.setValueAtTime(freq, start);
    g.gain.setValueAtTime(gain != null ? gain : 0.08, start);
    g.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    osc.connect(g);
    g.connect(c.destination);
    osc.start(start);
    osc.stop(start + duration + 0.02);
  }

  function playSound(name) {
    try {
      switch (name) {
        case 'checkpoint':
          tone(660, 0.15, 'sine', 0.09);
          tone(880, 0.2, 'sine', 0.09, 0.12);
          break;
        case 'buff-choice':
          tone(520, 0.14, 'triangle', 0.08);
          break;
        case 'attack-hit':
          tone(220, 0.18, 'sawtooth', 0.06);
          tone(140, 0.16, 'sawtooth', 0.05, 0.05);
          break;
        case 'attack-blocked':
          tone(180, 0.1, 'square', 0.05);
          break;
        case 'time-boost':
          tone(740, 0.1, 'sine', 0.08);
          tone(980, 0.15, 'sine', 0.08, 0.09);
          break;
        default:
          tone(500, 0.1, 'sine', 0.06);
      }
    } catch (e) { /* Web Audio unavailable; fail silently */ }
  }

  return { showLoading, hideLoading, playSound };
})();
