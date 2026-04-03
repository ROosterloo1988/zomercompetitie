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
    markMatchCompletion(target.closest('[data-match-entry]'));
  });

  bulkForm.addEventListener('change', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) {
      return;
    }
    markMatchCompletion(target.closest('[data-match-entry]'));
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
