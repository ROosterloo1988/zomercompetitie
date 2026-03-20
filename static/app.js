document.addEventListener('click', (event) => {
  const openButton = event.target.closest('[data-modal-open]');
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
      input.focus();
      input.select?.();
    }

    modal.showModal();
    return;
  }

  if (event.target.closest('[data-modal-close]')) {
    const modal = event.target.closest('dialog');
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
