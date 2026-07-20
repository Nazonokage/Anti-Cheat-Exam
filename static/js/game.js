// game.js — Game Mode UI: buffs (attack/defense/time-boost), the buff-CHOICE
// picker (earned every 5 correct answers — pick one, not all three), the
// live leaderboard tab, and the floating "you hit a milestone" notification.
// Loaded only on exam.html/review.html when the exam has game_mode enabled.
(function () {
  const bar = document.getElementById('game-bar');
  if (!bar) return;

  function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : null;
  }
  function csrfHeaders(extra) {
    return Object.assign({ 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') }, extra || {});
  }
  function playSound(name) { if (window.ExamUI) window.ExamUI.playSound(name); }

  const leaderboardUrl = bar.dataset.leaderboardUrl;
  const opponentsUrl = bar.dataset.opponentsUrl;
  const attackUrl = bar.dataset.attackUrl;
  const timeBoostUrl = bar.dataset.timeBoostUrl;
  const chooseBuffUrl = bar.dataset.chooseBuffUrl;

  const attackBtn = document.getElementById('buff-attack');
  const timeBoostBtn = document.getElementById('buff-timeboost');
  const rankToggle = document.getElementById('rank-toggle');
  const leaderboardPanel = document.getElementById('leaderboard-panel');
  const leaderboardRows = document.getElementById('leaderboard-rows');
  const opponentPicker = document.getElementById('opponent-picker');
  const opponentRows = document.getElementById('opponent-rows');
  const opponentCancel = document.getElementById('opponent-cancel');
  const toastRoot = document.getElementById('toast-root');
  const buffChoiceBanner = document.getElementById('buff-choice-banner');

  // --- Floating toast ------------------------------------------------------
  function showToast(html, ms) {
    const el = document.createElement('div');
    el.className = 'toast-float glass rounded-2xl px-5 py-3 shadow-2xl text-sm text-white max-w-xs text-center';
    el.innerHTML = html;
    toastRoot.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity 0.3s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 300);
    }, ms || 3200);
  }

  // --- Checkpoint notification (shown once, server already cleared it) ----
  // Fires every 5th CORRECT answer. The actual buff pick happens via the
  // persistent banner below (data-pending-buff-choice), not this toast —
  // the toast is just the "hey, you hit a milestone" heads-up.
  if (bar.dataset.checkpointNotice) {
    try {
      const notice = JSON.parse(bar.dataset.checkpointNotice);
      showToast(
        `<div class="font-semibold text-examaccent mb-1">🎉 ${notice.correct_count} correct!</div>` +
        `<div>You're rank <span class="font-mono text-white">#${notice.rank}</span> of ${notice.total}</div>` +
        `<div class="text-xs text-examtext/60 mt-1">Pick a buff below ⬇</div>`,
        4500
      );
      playSound('checkpoint');
    } catch (e) { /* malformed notice payload; skip silently */ }
  }

  // --- Buff choice: pick ONE of attack / defense / time_boost ---------------
  if (buffChoiceBanner) {
    buffChoiceBanner.querySelectorAll('.buff-choice-option').forEach(btn => {
      btn.addEventListener('click', async () => {
        buffChoiceBanner.querySelectorAll('.buff-choice-option').forEach(b => b.disabled = true);
        try {
          const res = await fetch(chooseBuffUrl, {
            method: 'POST', credentials: 'same-origin',
            headers: csrfHeaders(), body: JSON.stringify({ buff: btn.dataset.buff }),
          });
          const data = await res.json();
          if (!res.ok) {
            showToast(`<span class="text-red-300">${data.error || 'Could not claim buff'}</span>`);
            buffChoiceBanner.querySelectorAll('.buff-choice-option').forEach(b => b.disabled = false);
            return;
          }
          document.getElementById('buff-attack-count').textContent = data.attack_charges;
          document.getElementById('buff-defense-count').textContent = data.defense_charges;
          document.getElementById('buff-timeboost-count').textContent = data.time_boost_charges;
          if (data.attack_charges > 0) { attackBtn.disabled = false; attackBtn.classList.add('active'); attackBtn.classList.remove('disabled'); }
          if (data.time_boost_charges > 0) { timeBoostBtn.disabled = false; timeBoostBtn.classList.add('active'); timeBoostBtn.classList.remove('disabled'); }
          playSound('buff-choice');
          const labels = { attack: '⚔️ Attack', defense: '🛡️ Defense', time_boost: '⏱️ Time Boost' };
          showToast(`${labels[data.chosen] || 'Buff'} claimed!`, 1800);
          buffChoiceBanner.remove();
        } catch (e) {
          showToast('<span class="text-red-300">Network error — buff not claimed.</span>');
          buffChoiceBanner.querySelectorAll('.buff-choice-option').forEach(b => b.disabled = false);
        }
      });
    });
  }

  // --- Leaderboard panel -----------------------------------------------------
  let leaderboardOpen = false;
  let leaderboardPollTimer = null;

  async function refreshLeaderboard() {
    try {
      const res = await fetch(leaderboardUrl, { credentials: 'same-origin' });
      const data = await res.json();
      leaderboardRows.innerHTML = data.leaderboard.map(r => `
        <div class="flex items-center justify-between rounded-lg px-2 py-1 ${r.is_you ? 'bg-examprimary/15 border border-examprimary/30' : ''}">
          <span class="text-examtext/70 w-6">#${r.rank}</span>
          <span class="flex-1 text-white/90 ${r.is_you ? 'font-semibold' : ''}">${r.name}${r.is_you ? ' (you)' : ''}</span>
          <span class="font-mono text-examaccent">${r.score}</span>
        </div>`).join('');
    } catch (e) { /* keep last good render */ }
  }

  rankToggle.addEventListener('click', () => {
    leaderboardOpen = !leaderboardOpen;
    leaderboardPanel.classList.toggle('hidden', !leaderboardOpen);
    opponentPicker.classList.add('hidden');
    if (leaderboardOpen) {
      refreshLeaderboard();
      leaderboardPollTimer = setInterval(refreshLeaderboard, 5000);
    } else if (leaderboardPollTimer) {
      clearInterval(leaderboardPollTimer);
    }
  });

  // --- Attack: pick an opponent, then fire ----------------------------------
  attackBtn.addEventListener('click', async () => {
    if (attackBtn.disabled) return;
    leaderboardPanel.classList.add('hidden');
    opponentPicker.classList.remove('hidden');
    opponentRows.innerHTML = '<p class="text-examtext/40 text-xs">Loading…</p>';
    try {
      const res = await fetch(opponentsUrl, { credentials: 'same-origin' });
      const data = await res.json();
      if (!data.opponents.length) {
        opponentRows.innerHTML = '<p class="text-examtext/40 text-xs">No other active students right now.</p>';
        return;
      }
      opponentRows.innerHTML = data.opponents.map(o =>
        `<button type="button" data-target-id="${o.id}" class="opponent-row w-full text-left rounded-lg px-2 py-1.5 hover:bg-red-500/15 hover:text-red-200 text-white/90 transition-colors">⚔️ ${o.name}</button>`
      ).join('');
      opponentRows.querySelectorAll('.opponent-row').forEach(btn => {
        btn.addEventListener('click', () => doAttack(btn.dataset.targetId, btn.textContent.trim()));
      });
    } catch (e) {
      opponentRows.innerHTML = '<p class="text-red-300 text-xs">Couldn\'t load opponents.</p>';
    }
  });

  opponentCancel.addEventListener('click', () => opponentPicker.classList.add('hidden'));

  async function doAttack(targetId, label) {
    opponentPicker.classList.add('hidden');
    try {
      const res = await fetch(attackUrl, {
        method: 'POST', credentials: 'same-origin',
        headers: csrfHeaders(), body: JSON.stringify({ target_id: targetId }),
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(`<span class="text-red-300">${data.error || 'Attack failed'}</span>`);
        return;
      }
      const countEl = document.getElementById('buff-attack-count');
      if (countEl) countEl.textContent = data.attack_charges_left;
      if (data.attack_charges_left <= 0) {
        attackBtn.disabled = true;
        attackBtn.classList.remove('active');
        attackBtn.classList.add('disabled');
      }
      playSound(data.blocked ? 'attack-blocked' : 'attack-hit');
      showToast(data.blocked
        ? `🛡️ Blocked! ${data.target_name} had a defense charge ready.`
        : `⚔️ Hit! ${data.target_name} takes -1 to their score.`);
      if (leaderboardOpen) refreshLeaderboard();
    } catch (e) {
      showToast('<span class="text-red-300">Network error — attack not sent.</span>');
    }
  }

  // --- Time boost: +30s to the current question -----------------------------
  timeBoostBtn.addEventListener('click', async () => {
    if (timeBoostBtn.disabled) return;
    try {
      const res = await fetch(timeBoostUrl, { method: 'POST', credentials: 'same-origin', headers: csrfHeaders() });
      const data = await res.json();
      if (!res.ok) {
        showToast(`<span class="text-red-300">${data.error || "Couldn't use time boost"}</span>`);
        return;
      }
      playSound('time-boost');
      showToast('⏱️ +30 seconds added!', 1800);
      // Reload to pick up the new remaining time cleanly. Mark this as
      // intentional so mobile's spurious blur-on-navigate isn't miscounted
      // as a tab-switch (see exam.js).
      if (window.__examMarkIntentionalNav) window.__examMarkIntentionalNav();
      setTimeout(() => window.location.reload(), 900);
    } catch (e) {
      showToast('<span class="text-red-300">Network error — time boost not used.</span>');
    }
  });
})();
