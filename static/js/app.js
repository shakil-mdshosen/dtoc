/* Shared UI helpers: theme, nav, toasts, clipboard, confirmations. */
(function () {
  'use strict';

  const store = {
    get(key) { try { return window.localStorage.getItem(key); } catch (_) { return null; } },
    set(key, value) { try { window.localStorage.setItem(key, value); } catch (_) { /* storage unavailable */ } },
    remove(key) { try { window.localStorage.removeItem(key); } catch (_) { /* storage unavailable */ } },
  };

  function icon(name, cls) {
    return `<svg class="icon ${cls || ''}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function toast(message, iconName) {
    let host = document.querySelector('.toast-host');
    if (!host) {
      host = document.createElement('div');
      host.className = 'toast-host';
      host.setAttribute('role', 'status');
      document.body.appendChild(host);
    }
    const el = document.createElement('div');
    el.className = 'toast';
    el.innerHTML = icon(iconName || 'check-circle') + `<span>${escapeHtml(message)}</span>`;
    host.appendChild(el);
    setTimeout(() => el.remove(), 2600);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      const area = document.createElement('textarea');
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    toast('Link copied to clipboard');
  }

  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === 'light' || theme === 'dark') root.setAttribute('data-theme', theme);
    else root.removeAttribute('data-theme');
    const dark = theme === 'dark' || (!theme && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.querySelectorAll('[data-theme-toggle] use').forEach(u => u.setAttribute('href', dark ? '#i-sun' : '#i-moon'));
  }

  document.addEventListener('DOMContentLoaded', () => {
    applyTheme(store.get('dtoc-theme'));

    document.querySelectorAll('[data-theme-toggle]').forEach(btn => btn.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme')
        || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      const next = current === 'dark' ? 'light' : 'dark';
      store.set('dtoc-theme', next);
      applyTheme(next);
    }));

    const navToggle = document.querySelector('.nav-toggle');
    if (navToggle) navToggle.addEventListener('click', () => {
      const nav = document.querySelector('.nav');
      nav.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', nav.classList.contains('open'));
    });

    document.addEventListener('click', (e) => {
      const copyBtn = e.target.closest('[data-copy]');
      if (copyBtn) { e.preventDefault(); copyText(copyBtn.getAttribute('data-copy')); }
      const close = e.target.closest('.alert-close');
      if (close) close.closest('.alert').remove();
      // Close any open details-menu when clicking elsewhere.
      document.querySelectorAll('details.menu[open]').forEach(menu => {
        if (!menu.contains(e.target)) menu.removeAttribute('open');
      });
      const opener = e.target.closest('[data-open-dialog]');
      if (opener) {
        const dialog = document.getElementById(opener.getAttribute('data-open-dialog'));
        if (dialog) dialog.showModal();
      }
      const closer = e.target.closest('[data-close-dialog]');
      if (closer) closer.closest('dialog').close();
    });

    document.addEventListener('submit', (e) => {
      const message = e.target.getAttribute('data-confirm');
      if (message && !window.confirm(message)) e.preventDefault();
    });

    document.querySelectorAll('dialog.modal').forEach(dialog => {
      dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
    });
  });

  window.DTOC = { icon, escapeHtml, toast, copyText, store };
})();
