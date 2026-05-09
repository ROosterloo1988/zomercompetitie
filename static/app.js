const CLIENT_ID = localStorage.getItem('client_id') || crypto.randomUUID();
localStorage.setItem('client_id', CLIENT_ID);

function asElement(target) {
  return target instanceof Element ? target : null;
}

document.addEventListener('click', (event) => {
  const element = asElement(event.target);
  if (!element) return;

  const openButton = element.closest('[data-modal-open]');
  if (openButton) {
    const modal = document.getElementById(openButton.dataset.modalOpen);
    if (!modal) return;

    const form = modal.querySelector('form');
    if (form && openButton.dataset.formAction) {
      form.action = openButton.dataset.formAction;
    }

    const title = modal.querySelector('[data-modal-title]');
    if (title && openButton.dataset.modalTitle) {
      title.textContent = openButton.dataset.modalTitle;
    }

    const message = modal.querySelector('[data-modal-message]');
    if (message && openButton.dataset.modalMessage) {
      message.textContent = openButton.dataset.modalMessage;
    }

    const input = modal.querySelector('[data-modal-input]');
    if (input) {
      input.value = openButton.dataset.modalValue || '';
      requestAnimationFrame(() => {
        input.focus();
        input.select?.();
      });
    }

    modal.showModal();
    return;
  }

  if (element.closest('[data-modal-close]')) {
    const modal = element.closest('dialog');
    modal?.close();
  }
});

document.addEventListener('click', (event) => {
  const dialog = event.target;
  if (dialog instanceof HTMLDialogElement) {
    const rect = dialog.getBoundingClientRect();
    const inDialog =
      rect.top <= event.clientY &&
      event.clientY <= rect.top + rect.height &&
      rect.left <= event.clientX &&
      event.clientX <= rect.left + rect.width;
    if (!inDialog) {
      dialog.close();
    }
  }
});

const matchList = document.getElementById('match-card-list');
const bulkForm = document.getElementById('bulk-results-form');
const floatingSaveButton = document.getElementById('floating-save-button');

function updateMatchOrdering() {
  if (!matchList) return;
  const entries = Array.from(matchList.querySelectorAll('[data-match-entry]'));
  entries.sort((a, b) => {
    const aCompleted = a.dataset.completed === 'true';
    const bCompleted = b.dataset.completed === 'true';
    if (aCompleted === bCompleted) return 0;
    return aCompleted ? 1 : -1;
  });
  entries.forEach((entry) => matchList.appendChild(entry));
}

function markMatchCompletion(detailsElement) {
  if (!detailsElement) return;
  const scoreInputs = detailsElement.querySelectorAll('input[type="number"][name^="legs"]');
  const completed = Array.from(scoreInputs).some((input) => Number(input.value || 0) > 0);
  detailsElement.dataset.completed = completed ? 'true' : 'false';
  detailsElement.classList.toggle('is-completed', completed);
  detailsElement.classList.toggle('is-pending', !completed);
}

if (bulkForm && floatingSaveButton) {
  bulkForm.addEventListener('input', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) {
      return;
    }
    floatingSaveButton.hidden = false;
    floatingSaveButton.textContent = 'Wijzigingen opslaan';
   // markMatchCompletion(target.closest('[data-match-entry]'));
  });

  bulkForm.addEventListener('change', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) {
      return;
    }
   // markMatchCompletion(target.closest('[data-match-entry]'));
  });

  updateMatchOrdering();
}

const dashboardTabs = document.querySelectorAll('[data-dashboard-tab]');
const dashboardPanels = document.querySelectorAll('[data-dashboard-panel]');

function activateDashboardTab(tabKey) {
  if (!dashboardTabs.length || !dashboardPanels.length) return;
  dashboardTabs.forEach((tab) => {
    const active = tab.dataset.dashboardTab === tabKey;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  dashboardPanels.forEach((panel) => {
    const active = panel.dataset.dashboardPanel === tabKey;
    panel.hidden = !active;
  });
}

dashboardTabs.forEach((tab) => {
  tab.addEventListener('click', () => activateDashboardTab(tab.dataset.dashboardTab || 'overall'));
});

if (dashboardTabs.length && dashboardPanels.length) {
  activateDashboardTab(dashboardTabs[0].dataset.dashboardTab || 'overall');
}

function openFirstPendingMatch() {
  const firstPending = document.querySelector('[data-match-entry][data-completed="false"]');
  if (!firstPending) return;

  firstPending.open = true;
  firstPending.scrollIntoView({
    behavior: 'smooth',
    block: 'center'
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const url = new URL(window.location.href);

  if (url.searchParams.get('next') === '1') {
    openFirstPendingMatch();

    url.searchParams.delete('next');
    window.history.replaceState({}, '', url.toString());
  }
});

document.addEventListener('change', async (event) => {
  const el = event.target;

  if (!el.matches('.presence-toggle input[type="checkbox"]')) return;

  const playerId = el.dataset.playerId;
  const eveningId = el.dataset.eveningId;
  const present = el.checked;
  const label = el.closest('.presence-toggle')?.querySelector('.toggle-label');

  el.disabled = true;

  try {
    const response = await fetch(`/evenings/${eveningId}/attendance`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Requested-With': 'fetch',
      },
      body: new URLSearchParams({
          player_id: playerId,
          present: String(present),
          client_id: CLIENT_ID
        }),
    });

    if (!response.ok) {
      throw new Error('Opslaan mislukt');
    }

    if (label) {
      label.textContent = present ? 'Aanwezig' : 'Afwezig';
    }

    updateGenerateGroupsButton();
    
  } catch (err) {
    el.checked = !present;

    if (label) {
      label.textContent = !present ? 'Aanwezig' : 'Afwezig';
    }

    alert('Aanwezigheid opslaan mislukt.');
  } finally {
    el.disabled = false;
  }
});

document.addEventListener('change', async (event) => {
  const el = event.target;

  if (!el.matches('.player-active-toggle input[type="checkbox"]')) return;

  const playerId = el.dataset.playerId;
  const active = el.checked;
  const label = el.closest('.player-active-toggle')?.querySelector('.toggle-label');

  el.disabled = true;

  try {
    const response = await fetch(`/players/${playerId}/toggle`, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'fetch',
      },
    });

    if (!response.ok) {
      throw new Error('Opslaan mislukt');
    }

    if (label) {
      label.textContent = active ? 'Actief' : 'Inactief';
    }
  } catch (err) {
    el.checked = !active;

    if (label) {
      label.textContent = !active ? 'Actief' : 'Inactief';
    }

    alert('Spelerstatus opslaan mislukt.');
  } finally {
    el.disabled = false;
  }
});

function updateToggleBySelector(selector, checked, checkedLabel, uncheckedLabel) {
  const input = document.querySelector(selector);
  if (!input) return;

  input.checked = checked;

  const label = input.closest('label')?.querySelector('.toggle-label');
  if (label) {
    label.textContent = checked ? checkedLabel : uncheckedLabel;
  }
}

window.handleLiveMessage = function(rawMessage) {
  let message;

  try {
    message = JSON.parse(rawMessage);
  } catch {
    message = { type: rawMessage };
  }

  if (message.type === 'attendance_update') {
    updateToggleBySelector(
      `[data-attendance-toggle][data-player-id="${message.player_id}"]`,
      Boolean(message.present),
      'Aanwezig',
      'Afwezig'
    );

    updateGenerateGroupsButton();

    if (typeof showRefreshToast === 'function') {
      showRefreshToast('👥 Aanwezigheid gewijzigd. Klik hier om poule-opties te verversen.');
    }

    return;
  }

  if (message.type === 'player_active_update') {
    updateToggleBySelector(
      `[data-player-active-toggle][data-player-id="${message.player_id}"]`,
      Boolean(message.active),
      'Actief',
      'Inactief'
    );
    return;
  }
  
  if (message.type === 'live_match_input') {
    const input = document.querySelector(`[name="${CSS.escape(message.field_name)}"]`);
    if (!input) return;

    // Niet overschrijven als je zelf net in dat veld aan het typen bent
    if (document.activeElement === input) return;

    input.value = message.value;

    const matchEntry = input.closest('[data-match-entry]');
    if (matchEntry) {
     // markMatchCompletion(matchEntry);
    }

    if (floatingSaveButton) {
      floatingSaveButton.hidden = false;
      floatingSaveButton.textContent = 'Wijzigingen opslaan';
    }

    return;
  }

  if (message.type === 'score_saved') {
    if (floatingSaveButton) {
      floatingSaveButton.hidden = true;
    }

    document.querySelectorAll('[data-match-entry]').forEach((entry) => {
      markMatchCompletion(entry);
    });

    updateMatchOrdering();
    
    if (typeof showRefreshToast === 'function') {
      showRefreshToast('✅ Uitslagen opgeslagen. Klik hier om standen te verversen.');
    }
    
    return;
  }
  
  if (message.type === 'update' || rawMessage === 'update') {
    if (typeof showRefreshToast === 'function') {
      showRefreshToast('🔄 Nieuwe uitslagen beschikbaar! Klik hier.');
    }
  }
};

let liveSocket = null;
let liveSocketReady = false;

function connectSharedLiveSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = protocol + '//' + window.location.host + '/ws';

  liveSocket = new WebSocket(wsUrl + '?client_id=' + CLIENT_ID);

  liveSocket.onopen = function() {
    liveSocketReady = true;
  };

  liveSocket.onmessage = function(event) {
    if (window.handleLiveMessage) {
      window.handleLiveMessage(event.data);
    }
  };

  liveSocket.onclose = function() {
    liveSocketReady = false;
    setTimeout(connectSharedLiveSocket, 3000);
  };
}

connectSharedLiveSocket();

function sendLiveMessage(message) {
  if (!liveSocket || !liveSocketReady) return;

  liveSocket.send(JSON.stringify({
    ...message,
    client_id: CLIENT_ID
  }));
}

let liveInputTimer = null;

document.addEventListener('input', (event) => {
  const el = event.target;

  if (!el.matches('[data-live-match-input]')) return;

  clearTimeout(liveInputTimer);

  liveInputTimer = setTimeout(() => {
    sendLiveMessage({
      type: 'live_match_input',
      field_name: el.name,
      value: el.value,
      match_id: el.dataset.matchId || null
    });
  }, 150);
});

async function updateGenerateGroupsButton() {
  const button = document.getElementById('generate-groups-button');
  if (!button) return;

  const toggles = document.querySelectorAll('[data-attendance-toggle]');
  const presentCount = document.querySelectorAll('[data-attendance-toggle]:checked').length;

  button.hidden = presentCount < 3;

  if (presentCount < 3 || toggles.length === 0) return;

  const eveningId = toggles[0].dataset.eveningId;
  if (!eveningId) return;

  try {
    const response = await fetch(`/evenings/${eveningId}/group-options`, {
      headers: {
        'X-Requested-With': 'fetch',
      },
    });

    if (!response.ok) return;

    const data = await response.json();

    renderGroupOptions(data.single_options || [], data.koppel_options || [], eveningId);
  } catch (err) {
    console.error('Poule-opties ophalen mislukt', err);
  }
}

function renderGroupOptions(singleOptions, koppelOptions, eveningId) {
  const singleList = document.querySelector('#list-single .clean-list');
  const koppelList = document.querySelector('#list-koppel .clean-list');
  const koppelPanel = document.getElementById('list-koppel');
  const formatPanel = document.querySelector('.format-toggle-panel');

  if (singleList) {
    singleList.innerHTML = singleOptions.map((opt) => optionRowHtml(opt, eveningId, 'single')).join('');
  }

  if (koppelList) {
    koppelList.innerHTML = koppelOptions.map((opt) => optionRowHtml(opt, eveningId, 'koppel')).join('');
  }

  if (koppelPanel) {
    koppelPanel.classList.toggle('is-hidden', koppelOptions.length === 0);
  }

  if (formatPanel) {
    formatPanel.hidden = koppelOptions.length === 0;
  }
}

function optionRowHtml(opt, eveningId, format) {
  return `
    <li class="option-row">
      <div>
        <strong class="option-title">${escapeHtml(opt.description)}</strong>
        <span class="muted small">Totaal ${opt.total_matches} wedstrijden</span>
      </div>
      <form method="post" action="/evenings/${eveningId}/groups" class="no-margin">
        <input type="hidden" name="config" value="${escapeHtml(opt.config)}">
        <input type="hidden" name="format" value="${format}">
        <button type="submit" class="button secondary small">Kies deze</button>
      </form>
    </li>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function syncLateKoppelSelects() {
  const select1 = document.querySelector('#late-koppel-form select[name="player1_id"]');
  const select2 = document.querySelector('#late-koppel-form select[name="player2_id"]');

  if (!select1 || !select2) return;

  const value1 = select1.value;
  const value2 = select2.value;

  Array.from(select1.options).forEach((option) => {
    option.disabled = option.value !== '' && option.value === value2;
  });

  Array.from(select2.options).forEach((option) => {
    option.disabled = option.value !== '' && option.value === value1;
  });
}

document.addEventListener('change', (event) => {
  const el = event.target;

  if (
    el.matches('#late-koppel-form select[name="player1_id"]') ||
    el.matches('#late-koppel-form select[name="player2_id"]')
  ) {
    syncLateKoppelSelects();
  }
});

document.addEventListener('click', (event) => {
  const element = asElement(event.target);
  if (!element) return;

  const openButton = element.closest('[data-modal-open="late-entry-modal"]');
  if (openButton) {
    setTimeout(syncLateKoppelSelects, 0);
  }
});
