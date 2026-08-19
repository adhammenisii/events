/** The transient message strip at the bottom of the booking floor. */

const ICONS = { success: '✓', error: '!', info: 'i' };
const VISIBLE_MS = 3600;

export class Toast {
  constructor(root, iconElement, messageElement) {
    this.root = root;
    this.icon = iconElement;
    this.message = messageElement;
    this.hideTimer = null;
  }

  show(message, kind = 'info') {
    this.message.textContent = message;
    this.icon.textContent = ICONS[kind] ?? ICONS.info;
    this.root.className = `toast toast--visible toast--${kind}`;

    // Restart the countdown on every message, so a burst of rapid clicks
    // does not hide the last one early.
    clearTimeout(this.hideTimer);
    this.hideTimer = setTimeout(() => this.root.classList.remove('toast--visible'), VISIBLE_MS);
  }

  success(message) { this.show(message, 'success'); }
  error(message) { this.show(message, 'error'); }
  info(message) { this.show(message, 'info'); }
}
