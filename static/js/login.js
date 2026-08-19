/**
 * Sign-in page controller.
 *
 * One form serves both signing in and creating an account; switching modes
 * changes the labels and which endpoint the submit hits, rather than swapping
 * in a second form the browser would treat as unrelated.
 */

import { api, ApiError } from './api.js';

const elements = {
  form: document.getElementById('auth-form'),
  intro: document.getElementById('auth-intro'),
  error: document.getElementById('auth-error'),
  nameField: document.getElementById('name-field'),
  fullName: document.getElementById('full-name'),
  email: document.getElementById('email'),
  password: document.getElementById('password'),
  submit: document.getElementById('submit-button'),
  switchPrompt: document.getElementById('switch-prompt'),
  switchMode: document.getElementById('switch-mode'),
  demoSection: document.getElementById('demo-accounts'),
  demoList: document.getElementById('demo-account-list'),
  demoHint: document.getElementById('demo-hint'),
};

const MODES = {
  signIn: {
    intro: 'Sign in to book seats.',
    submit: 'Sign in',
    switchPrompt: 'No account yet?',
    switchAction: 'Create one',
    passwordAutocomplete: 'current-password',
  },
  register: {
    intro: 'Create an account to start booking.',
    submit: 'Create account',
    switchPrompt: 'Already registered?',
    switchAction: 'Sign in',
    passwordAutocomplete: 'new-password',
  },
};

let mode = 'signIn';
let submitting = false;

function applyMode() {
  const copy = MODES[mode];
  elements.intro.textContent = copy.intro;
  elements.submit.textContent = copy.submit;
  elements.switchPrompt.textContent = copy.switchPrompt;
  elements.switchMode.textContent = copy.switchAction;
  elements.nameField.hidden = mode !== 'register';
  elements.fullName.required = mode === 'register';
  elements.password.autocomplete = copy.passwordAutocomplete;
  elements.demoSection.hidden = mode !== 'signIn' || !elements.demoList.childElementCount;
  hideError();
}

function showError(message) {
  elements.error.textContent = message;
  elements.error.classList.add('auth-error--visible');
}

function hideError() {
  elements.error.classList.remove('auth-error--visible');
}

function setSubmitting(isSubmitting) {
  submitting = isSubmitting;
  elements.submit.disabled = isSubmitting;
  elements.submit.textContent = isSubmitting
    ? (mode === 'signIn' ? 'Signing in…' : 'Creating account…')
    : MODES[mode].submit;
}

elements.switchMode.addEventListener('click', () => {
  mode = mode === 'signIn' ? 'register' : 'signIn';
  applyMode();
  (mode === 'register' ? elements.fullName : elements.email).focus();
});

elements.form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (submitting) return;

  const email = elements.email.value.trim();
  const password = elements.password.value;
  const fullName = elements.fullName.value.trim();

  if (!email || !password || (mode === 'register' && !fullName)) {
    showError('Fill in every field to continue.');
    return;
  }

  hideError();
  setSubmitting(true);
  try {
    if (mode === 'signIn') {
      await api.signIn(email, password);
    } else {
      await api.register(fullName, email, password);
    }
    // replace() rather than assign(): the login page should not sit in
    // history for the back button to return to once signed in.
    window.location.replace('/');
  } catch (error) {
    setSubmitting(false);
    showError(error instanceof ApiError ? error.message : 'Something went wrong. Try again.');
    elements.password.select();
  }
});

async function loadDemoAccounts() {
  try {
    const { accounts, password } = await api.demoAccounts();
    if (!accounts?.length) return;

    for (const account of accounts) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'demo-account';
      chip.textContent = account.full_name;
      chip.title = account.email;
      chip.addEventListener('click', () => {
        elements.email.value = account.email;
        elements.password.value = password;
        elements.password.focus();
      });
      elements.demoList.append(chip);
    }
    elements.demoHint.textContent = `Password: ${password}`;
    elements.demoSection.hidden = mode !== 'signIn';
  } catch {
    // Sample accounts are a convenience for the demo data. If the lookup
    // fails the form still works, so there is nothing to report here.
  }
}

applyMode();
loadDemoAccounts();
elements.email.focus();
