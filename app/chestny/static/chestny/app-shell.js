'use strict';
(() => {
  const pages = ['work', 'history', 'settings'];
  function showPage() {
    const page = pages.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'work';
    document.querySelectorAll('[data-panel]').forEach(el => { el.hidden = el.dataset.panel !== page; });
    document.querySelectorAll('[data-page]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.page === page)));
  }
  document.querySelectorAll('[data-page]').forEach(el => el.addEventListener('click', () => { location.hash = el.dataset.page; }));
  window.addEventListener('hashchange', showPage); showPage();
  const profile = document.getElementById('profile');
  let turnoverBusy = false;
  function syncBusy() {
    profile.disabled = turnoverBusy || [...document.querySelectorAll('.profile-btn')].some(el => el.disabled);
  }
  document.addEventListener('turnover-ui-busy', e => { turnoverBusy = e.detail; syncBusy(); });
  const observer = new MutationObserver(syncBusy);
  document.querySelectorAll('.profile-btn').forEach(el => observer.observe(el, {attributes: true, attributeFilter: ['disabled']}));
  syncBusy();
  profile.addEventListener('change', () => {
    const tab = [...document.querySelectorAll('.profile-btn')].find(el => el.dataset.profile === profile.value);
    if (tab) tab.click();
  });
  document.querySelectorAll('[data-operation]').forEach(button => button.addEventListener('click', () => {
    const operation = document.getElementById('operation');
    if (operation.value === button.dataset.operation) return;
    operation.value = button.dataset.operation; operation.dispatchEvent(new Event('change'));
    document.querySelectorAll('[data-operation]').forEach(el => el.setAttribute('aria-pressed', String(el === button)));
  }));
})();
