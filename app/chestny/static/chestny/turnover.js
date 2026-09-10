'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let token = null, events = [], reviewed = null;
  const checks = new Map();
  const decisions = new Map();
  const labels = {PREPARED:'Не отправлен', SENDING:'Отправляется', SENT:'На обработке',
    UNKNOWN:'Требует сверки', VERIFYING:'Проверка кодов', CONFIRMED:'Подтверждён',
    FAILED:'Отклонён', CANCELLED:'Отменён до отправки'};
  async function api(url, data, method = 'POST') {
    const opts = {method};
    if (data instanceof FormData) opts.body = data;
    else if (data !== undefined) { opts.headers = {'Content-Type': 'application/json'}; opts.body = JSON.stringify(data); }
    const response = await fetch(url, opts);
    if (response.status === 204) return {};
    const body = await response.json();
    if (!response.ok) throw new Error(body.message || 'Операция не выполнена');
    return body;
  }
  function notice(message) { $('notice').textContent = message; }
  function fieldsVisibility() {
    const sale = $('operation').value === 'Продажа';
    $('sale-auto').hidden = !sale;
    if ($('return-auto')) $('return-auto').hidden = sale;
    $('fact-text').textContent = sale
      ? 'Подтверждаю выбранные дистанционные продажи и данные отчёта WB.'
      : 'Подтверждаю факт выбранных возвратов, их соответствие последней продаже и сохранность маркировки. Указанные дата, оплата и документы проверены.';
  }
  function updateReadiness() {
    const data = payload(), fields = data.defaults;
    if ($('selected-count')) $('selected-count').textContent = `Выбрано: ${data.selected_rows.length}`;
    const missing = [];
    if (!data.selected_rows.length) missing.push('выберите строки для обработки');
    if (events.some(e => data.selected_rows.includes(e.row_index) && e.operation === 'Возврат' && e.paid === null)) missing.push('уточните строки с неполными реквизитами чека');
    if (!fields.event_date && events.some(e => data.selected_rows.includes(e.row_index) && !e.date)) {
      missing.push('укажите дату события для строк без даты в файле');
    }
    if (!fields.event_confirmed) missing.push('поставьте галочку подтверждения выбранных событий');
    const ready = reviewed !== null && reviewed === JSON.stringify(data);
    $('submit').disabled = !ready;
    $('submit-help').textContent = ready
      ? 'Предпросмотр готов. Можно подписать и отправить документы.'
      : missing.length
        ? 'Для подготовки документов: ' + missing.join('; ') + '. Затем нажмите «Предпросмотр документов».'
        : 'Нажмите «Предпросмотр документов». После успешной подготовки включится кнопка «Подписать и отправить».';
  }
  function resetReview() { reviewed = null; $('prepared').textContent = ''; updateReadiness(); }
  function payload() {
    return {selected_rows: [...document.querySelectorAll('#rows input:checked')].map(x => Number(x.value)),
      defaults: {event_date: $('event-date').value || null, event_confirmed: $('fact').checked}};
  }

  function price(event) {
    if (event.cost_kopecks === null || event.cost_kopecks === undefined) return 'Не указана';
    const amount = Number(event.cost_kopecks) / 100;
    const currency = event.currency === 'RUB' ? '₽' : event.currency;
    return `${amount.toLocaleString('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2})} ${currency || ''}`.trim();
  }

  function renderRows() {
    $('rows').replaceChildren();
    for (const event of events.filter(e => e.operation === $('operation').value)) {
      const tr = document.createElement('tr');
      const td = document.createElement('td'), input = document.createElement('input');
      input.type = 'checkbox'; input.value = event.row_index;
      input.setAttribute('aria-label', `Выбрать строку ${event.row_index}`);
      input.addEventListener('change', resetReview); td.append(input); tr.append(td);
      for (const value of [event.row_index, event.mask, event.assignment, event.date || 'Не указана', price(event), event.receipt || 'Нет', event.operation === 'Продажа' ? '—' : event.paid === null ? 'Неполные реквизиты' : event.paid ? 'После оплаты' : 'Без оплаты', checks.get(event.row_index) || 'Не проверено']) {
        const cell = document.createElement('td'); cell.textContent = value; tr.append(cell);
      }
      $('rows').append(tr);
    }
    resetReview();
  }
  async function history() {
    $('documents').replaceChildren();
    const body = await api('/api/turnover/documents?profile_id=' + encodeURIComponent($('profile').value), undefined, 'GET');
    $('documents').replaceChildren();
    if (!body.documents.length) {
      const empty = document.createElement('p'); empty.className = 'empty-history';
      empty.textContent = 'Документов продаж и возвратов для этого профиля пока нет.';
      $('documents').append(empty);
    }
    for (const doc of body.documents) {
      const article = document.createElement('article');
      const text = document.createElement('p');
      text.textContent = `${doc.operation}: ${doc.count} кодов · ${doc.environment === 'sandbox' ? 'Демо' : 'Промышленный'} · ${labels[doc.state] || 'Требует проверки'} · ${doc.message} · ID ЧЗ: ${doc.document_id || 'не получен'}`;
      const button = document.createElement('button'); button.textContent = 'Сверить';
      button.disabled = ['CONFIRMED', 'PREPARED', 'CANCELLED'].includes(doc.state);
      button.onclick = () => run(async () => {
        const data = {};
        if (!doc.document_id) {
          const id = window.prompt('Введите ID соответствующего документа из ЛК ЧЗ. Состав и реквизиты будут проверены.');
          if (!id) return;
          data.document_id = id.trim();
        }
        await api(`/api/turnover/documents/${doc.id}/reconcile`, data); await history();
      });
      const link = document.createElement('a'); link.href = `/api/turnover/documents/${doc.id}/report`; link.textContent = ' Скачать XLSX';
      article.append(text, button, link); $('documents').append(article);
      if (doc.state === 'PREPARED') {
        const cancel = document.createElement('button'); cancel.textContent = 'Отменить неотправленный';
        cancel.onclick = () => run(async () => {
          await api(`/api/turnover/documents/${doc.id}/cancel-unsent`, {}); await history();
        });
        article.append(cancel);
      }
      if (doc.state === 'FAILED') {
        const release = document.createElement('button'); release.textContent = 'Проверить отказ и освободить для исправления';
        release.onclick = () => run(async () => {
          await api(`/api/turnover/documents/${doc.id}/release-rejected`, {}); await history();
        });
        article.append(release);
      }
    }
  }
  let busy = false;
  async function run(fn, action = '') {
    const result = $('action-result');
    const actionNotice = (message, error = false) => {
      if (!action || !result) return;
      result.hidden = false;
      result.dataset.error = String(error);
      result.textContent = message;
    };
    if (busy) { actionNotice('Предыдущая операция ещё выполняется. Дождитесь её завершения.'); return; }
    actionNotice(action + '…');
    busy = true; notice('Выполняется…');
    $('notice').dataset.error = 'false';
    document.dispatchEvent(new CustomEvent('turnover-ui-busy', {detail: true}));
    try { await fn(); notice('Готово'); actionNotice(action + ': готово.'); }
    catch (error) { $('notice').dataset.error = 'true'; notice(error.message); actionNotice(action + ': ' + error.message, true); }
    finally { busy = false; document.dispatchEvent(new CustomEvent('turnover-ui-busy', {detail: false})); }
  }
  $('upload').onclick = () => run(async () => {
    const file = $('file').files[0]; if (!file) throw new Error('Выберите XLSX');
    if (token) { await api('/api/turnover/import/' + token, undefined, 'DELETE'); token = null; }
    const form = new FormData(); form.append('file', file); form.append('profile_id', $('profile').value);
    const body = await api('/api/turnover/import', form); token = body.token; events = body.events; checks.clear(); decisions.clear();
    $('summary').textContent = Object.entries(body.counts).map(([op, count]) => `${op || 'Без операции'}: ${count}`).join(' · ') + ` · Исключено при разборе: ${body.excluded.length}`;
    $('workspace').hidden = false; $('fact').checked = false; renderRows();
  });
  $('profile').onchange = () => run(async () => {
    if (token) await api('/api/turnover/import/' + token, undefined, 'DELETE');
    token = null; events = []; $('workspace').hidden = true; resetReview(); await history();
  });
  $('operation').onchange = () => {
    $('fact').checked = false; fieldsVisibility(); renderRows();
  };
  $('select').onclick = () => { document.querySelectorAll('#rows input').forEach(x => x.checked = true); resetReview(); };
  $('clear').onclick = () => { document.querySelectorAll('#rows input').forEach(x => x.checked = false); resetReview(); };
  $('ready').onclick = () => { document.querySelectorAll('#rows input').forEach(x => x.checked = decisions.get(Number(x.value)) === 'READY'); resetReview(); };
  document.querySelectorAll('fieldset input,fieldset select').forEach(el => el.addEventListener('input', resetReview));
  $('check').onclick = () => run(async () => {
    const selected = payload().selected_rows;
    const body = await api(`/api/turnover/import/${token}/check`, payload());
    body.rows.forEach(row => { decisions.set(row.row_index, row.state); checks.set(row.row_index, row.message); });
    renderRows(); document.querySelectorAll('#rows input').forEach(x => x.checked = selected.includes(Number(x.value)));
    updateReadiness();
  }, 'Проверка КИЗов');
  $('prepare').onclick = () => run(async () => {
    resetReview();
    const data = payload(); const body = await api(`/api/turnover/import/${token}/prepare`, data);
    reviewed = JSON.stringify(data); $('prepared').textContent = body.documents.map(d => `Документ ${d.index}: ${d.operation}, ${d.count} кодов${d.date ? ', дата ' + d.date : ''}`).join(' · ');
    updateReadiness();
  }, 'Подготовка документов');
  $('submit').onclick = () => run(async () => {
    const data = payload(); if (JSON.stringify(data) !== reviewed) throw new Error('Обновите предпросмотр документов');
    if (!window.confirm($('prepared').textContent + '\nПодписать и отправить в Честный знак?')) return;
    resetReview(); await api(`/api/turnover/import/${token}/submit`, {...data, confirmed: true});
    await history();
  });
  $('history').onclick = () => run(history);
  run(async () => {
    fieldsVisibility();
    const body = await api('/api/turnover/profiles', undefined, 'GET');
    $('environment').textContent = body.environment === 'sandbox' ? 'Демонстрационный контур ЧЗ. Реальный оборот не изменяется.' : 'Промышленный контур ЧЗ. Подтверждённая отправка изменяет реальный оборот.';
    body.profiles.forEach(p => { const option = document.createElement('option'); option.value = p.id; option.textContent = p.name; $('profile').append(option); });
    await history();
  });
})();
