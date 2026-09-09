(function() {
"use strict";

var profileId = "org-sinyavin";
var gen = 0;
var abort = null;
var certOk = false;
var activeImport = null;   // {token, profileId}
var profileWorkspaces = Object.create(null);

function workspace(pid) {
  if (!profileWorkspaces[pid]) {
    profileWorkspaces[pid] = {
      certOk: false, activeImport: null, preview: null, selectedRows: []
    };
  }
  return profileWorkspaces[pid];
}

function saveCurrentWorkspace() {
  var ws = workspace(profileId);
  ws.certOk = certOk;
  ws.activeImport = activeImport;
}

var els = {};
var btnEls = [];
var tabEls = [];

function init() {
  els.form = document.getElementById("settings-form");
  els.inn = document.getElementById("inn");
  els.fias = document.getElementById("fias-id");
  els.cert = document.getElementById("certificate");
  els.status = document.getElementById("status-area");
  els.error = document.getElementById("error-area");

  els.uploadSection = document.getElementById("upload-section");
  els.gateNote = document.getElementById("gate-note");
  els.uploadControls = document.getElementById("upload-controls");
  els.fileInput = document.getElementById("file-input");
  els.uploadBtn = document.getElementById("upload-btn");
  els.uploadBusy = document.getElementById("upload-busy");
  els.uploadError = document.getElementById("upload-error");
  els.dryrunResults = document.getElementById("dryrun-results");
  els.dryrunSummary = document.getElementById("dryrun-summary");
  els.dryrunTables = document.getElementById("dryrun-tables");
  els.submissionWorkspace = document.getElementById("submission-workspace");
  els.excludedResults = document.getElementById("excluded-results");
  els.cancelImportBtn = document.getElementById("cancel-import-btn");
  els.submitCzBtn = document.getElementById("submit-cz-btn");
  els.actionDate = document.getElementById("action-date");
  els.documentNumber = document.getElementById("document-number");
  els.documentDate = document.getElementById("document-date");
  els.documentName = document.getElementById("document-name");
  els.selectAllKiz = document.getElementById("select-all-kiz");
  els.selectAllBtn = document.getElementById("select-all-btn");
  els.clearSelectionBtn = document.getElementById("clear-selection-btn");
  els.selectionSummary = document.getElementById("selection-summary");

  btnEls = [
    els.form.querySelector(".btn-primary"),
    document.getElementById("refresh-certs"),
    document.getElementById("check-cert"),
  ];
  tabEls = Array.prototype.slice.call(document.querySelectorAll(".profile-btn"));

  for (var i = 0; i < tabEls.length; i++) {
    tabEls[i].addEventListener("click", onTabClick);
  }

  els.form.addEventListener("submit", onSave);
  btnEls[1].addEventListener("click", onRefreshCerts);
  btnEls[2].addEventListener("click", onCheckCert);

  els.uploadBtn.addEventListener("click", onUpload);
  els.cancelImportBtn.addEventListener("click", onCancelImport);
  els.submitCzBtn.addEventListener("click", onSubmitCz);
  els.selectAllKiz.addEventListener("change", function() {
    setAllKizSelected(els.selectAllKiz.checked);
  });
  els.selectAllBtn.addEventListener("click", function() { setAllKizSelected(true); });
  els.clearSelectionBtn.addEventListener("click", function() { setAllKizSelected(false); });

  els.cert.addEventListener("change", function() {
    certOk = false;
    saveCurrentWorkspace();
    updateGate();
  });

  loadProfile(profileId);
  loadReport();
}

function setBusy(busy) {
  for (var i = 0; i < btnEls.length; i++) {
    btnEls[i].disabled = busy;
  }
  for (var j = 0; j < tabEls.length; j++) {
    tabEls[j].disabled = busy;
  }
  els.uploadBtn.disabled = busy;
  els.fileInput.disabled = busy;
}

function showStatus(msg) {
  els.error.style.display = "none";
  els.error.textContent = "";
  els.status.textContent = msg;
  els.status.style.display = "block";
}

function showError(msg) {
  els.status.style.display = "none";
  els.status.textContent = "";
  els.error.textContent = msg;
  els.error.style.display = "block";
}

function clearAll() {
  els.status.style.display = "none";
  els.status.textContent = "";
  els.error.style.display = "none";
  els.error.textContent = "";
}

function clearUpload() {
  els.uploadError.style.display = "none";
  els.uploadError.textContent = "";
  els.uploadBusy.style.display = "none";
  els.dryrunResults.style.display = "none";
  els.uploadControls.style.display = "none";
  els.gateNote.style.display = "block";
}

function updateGate() {
  if (certOk) {
    els.uploadSection.classList.remove("disabled");
    els.gateNote.style.display = "none";
    els.uploadControls.style.display = "block";
  } else {
    els.uploadSection.classList.add("disabled");
    els.gateNote.style.display = "block";
    els.uploadControls.style.display = "none";
    els.uploadBusy.style.display = "none";
    els.uploadError.style.display = "none";
    els.dryrunResults.style.display = "none";
  }
}

function confirmIfActiveImport() {
  if (activeImport !== null) {
    return confirm("Есть активный импорт. Отменить его и продолжить?");
  }
  return true;
}

function cancelActiveImport(cb) {
  if (activeImport === null) { cb(true); return; }

  var xhr = new XMLHttpRequest();
  xhr.open("DELETE", "/api/imports/" + encodeURIComponent(activeImport.token), true);
  xhr.onload = function() {
    if (xhr.status === 204) {
      activeImport = null;
      workspace(profileId).activeImport = null;
      workspace(profileId).preview = null;
      workspace(profileId).selectedRows = [];
      cb(true);
    } else {
      cb(false);
    }
  };
  xhr.onerror = function() { cb(false); };
  xhr.send();
}

function onTabClick(e) {
  var btn = e.currentTarget;
  var pid = btn.getAttribute("data-profile");
  if (pid === profileId) return;

  saveCurrentWorkspace();
  for (var i = 0; i < tabEls.length; i++) {
    tabEls[i].classList.remove("active");
    tabEls[i].setAttribute("aria-selected", "false");
  }
  btn.classList.add("active");
  btn.setAttribute("aria-selected", "true");

  profileId = pid;
  var ws = workspace(pid);
  certOk = ws.certOk;
  activeImport = ws.activeImport;
  clearUpload();
  els.fileInput.value = "";
  updateGate();
  if (certOk && activeImport && ws.preview) {
    showDryRun(ws.preview);
    els.uploadControls.style.display = "none";
  }
  loadProfile(pid);
  loadReport();
}

function validateAndBuildBody() {
  var innVal = els.inn.value.trim();
  var fiasVal = els.fias.value.trim();
  var certVal = els.cert.value;

  if (innVal !== "" && !/^\d{12}$/.test(innVal)) {
    showError("ИНН должен содержать ровно 12 цифр или быть пустым");
    return null;
  }
  if (fiasVal !== "" && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(fiasVal)) {
    showError("FIAS ID должен быть корректным UUID или пустым");
    return null;
  }

  var body = {};
  body.inn = innVal || "";
  body.fias_id = fiasVal || "";
  body.certificate_thumbprint = certVal || "";
  return body;
}

function done(myGen) {
  if (myGen !== undefined && myGen !== gen) return;
  setBusy(false);
}

function loadProfile(pid) {
  gen++;
  var myGen = gen;
  clearAll();
  setBusy(true);

  if (abort) { abort.abort(); abort = null; }
  abort = new AbortController();

  fetch("/api/profiles/" + encodeURIComponent(pid), { signal: abort.signal })
    .then(function(r) {
      if (!r.ok) throw new Error();
      return r.json();
    })
    .then(function(data) {
      if (myGen !== gen) return;
      els.inn.value = data.inn || "";
      els.fias.value = data.fias_id || "";

      var tp = data.certificate_thumbprint || "";
      var existingOpt = els.cert.querySelector('option[value="' + tp.replace(/"/g, "") + '"]');
      if (tp && !existingOpt) {
        var savedOpt = document.createElement("option");
        savedOpt.value = tp;
        savedOpt.textContent = "Сохранён: …" + tp.slice(-8);
        els.cert.appendChild(savedOpt);
      }
      els.cert.value = tp;

      done(myGen);
    })
    .catch(function(err) {
      if (err.name === "AbortError") { return; }
      if (myGen !== gen) return;
      showError("Ошибка загрузки профиля");
      done(myGen);
    });
}

function onSave(e) {
  e.preventDefault();
  clearAll();

  var body = validateAndBuildBody();
  if (!body) return;

  var myGen = gen;
  setBusy(true);
  fetch("/api/profiles/" + encodeURIComponent(profileId), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
    .then(function(r) {
      if (!r.ok) throw new Error();
      return r.json();
    })
    .then(function(data) {
      if (myGen !== gen) return;
      els.inn.value = data.inn || "";
      els.fias.value = data.fias_id || "";
      els.cert.value = data.certificate_thumbprint || "";

      certOk = false;
      saveCurrentWorkspace();
      updateGate();
      showStatus("Настройки сохранены");
      done(myGen);
    })
    .catch(function() {
      if (myGen !== gen) return;
      showError("Ошибка сохранения настроек");
      done(myGen);
    });
}

function onRefreshCerts() {
  clearAll();

  var myGen = gen;
  setBusy(true);

  fetch("/api/certificates")
    .then(function(r) {
      if (!r.ok) throw new Error();
      return r.json();
    })
    .then(function(certs) {
      if (myGen !== gen) return;
      if (!Array.isArray(certs)) throw new Error();
      var currentVal = els.cert.value;
      els.cert.textContent = "";
      var emptyOpt = document.createElement("option");
      emptyOpt.value = "";
      emptyOpt.textContent = "— выберите сертификат —";
      els.cert.appendChild(emptyOpt);

      var found = false;
      for (var i = 0; i < certs.length; i++) {
        var c = certs[i];
        var opt = document.createElement("option");
        opt.value = c.thumbprint;
        var label = (c.subject || "") + " / " + (c.store || "") + " …" + (c.thumbprint ? c.thumbprint.slice(-8) : "");
        opt.textContent = label;
        els.cert.appendChild(opt);
        if (c.thumbprint === currentVal) found = true;
      }

      if (currentVal && !found) {
        var missingOpt = document.createElement("option");
        missingOpt.value = currentVal;
        missingOpt.textContent = "★ не найден: …" + currentVal.slice(-8);
        els.cert.appendChild(missingOpt);
        els.cert.value = currentVal;
      } else if (currentVal && found) {
        els.cert.value = currentVal;
      }

      certOk = false;
      saveCurrentWorkspace();
      updateGate();
      showStatus("Сертификаты обновлены: " + certs.length);
      done(myGen);
    })
    .catch(function() {
      if (myGen !== gen) return;
      showError("Ошибка загрузки сертификатов");
      done(myGen);
    });
}

function onCheckCert() {
  clearAll();

  var body = validateAndBuildBody();
  if (!body) return;

  var myGen = gen;
  setBusy(true);

  fetch("/api/profiles/" + encodeURIComponent(profileId), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
    .then(function(r) {
      if (!r.ok) throw new Error();
      if (myGen !== gen) return;
      return fetch("/api/profiles/" + encodeURIComponent(profileId) + "/certificate/diagnose", {
        method: "POST",
      });
    })
    .then(function(r) {
      if (!r.ok) throw new Error();
      return r.json();
    })
    .then(function(diag) {
      if (myGen !== gen) return;
      var parts = [];
      parts.push("Настроен: " + (diag.configured ? "да" : "нет"));
      if (diag.found) {
        parts.push("Найден: да");
        parts.push("Закрытый ключ: " + (diag.has_private_key ? "есть" : "нет"));
        parts.push("Тестовая подпись: " + (diag.can_sign ? "успешно" : "недоступна"));
      } else {
        parts.push("Найден: нет");
      }

      if (diag.configured && diag.found && diag.has_private_key && diag.can_sign) {
        certOk = true;
        saveCurrentWorkspace();
        updateGate();
      } else {
        certOk = false;
        saveCurrentWorkspace();
        updateGate();
      }

      showStatus(parts.join(" | "));
      done(myGen);
    })
    .catch(function() {
      if (myGen !== gen) return;
      showError("Ошибка проверки сертификата");
      done(myGen);
    });
}

// ── Dry-run upload ──────────────────────────────────────────────────────────

function onUpload() {
  var file = els.fileInput.files[0];
  if (!file) {
    showUploadError("Выберите файл .xlsx");
    return;
  }
  if (!file.name.toLowerCase().endsWith(".xlsx")) {
    showUploadError("Поддерживаются только файлы .xlsx");
    return;
  }

  var myGen = gen;

  if (!confirmIfActiveImport()) return;

  cancelActiveImport(function(ok) {
    if (!ok || myGen !== gen) return;
    doUpload(myGen);
  });
}

function doUpload(myGen) {
  var file = els.fileInput.files[0];
  if (!file) return;

  els.uploadError.style.display = "none";
  els.uploadError.textContent = "";
  els.dryrunResults.style.display = "none";
  els.uploadControls.style.display = "none";
  els.uploadBusy.style.display = "block";

  var fd = new FormData();
  fd.append("profile_id", profileId);
  fd.append("file", file);

  var xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/imports/preview", true);

  xhr.onload = function() {
    if (myGen !== gen) return;
    els.uploadBusy.style.display = "none";

    if (xhr.status === 201) {
      var data = JSON.parse(xhr.responseText);
      activeImport = { token: data.import_token, profileId: profileId };
      workspace(profileId).activeImport = activeImport;
      workspace(profileId).preview = data;
      workspace(profileId).selectedRows = data.accepted.map(function(row) {
        return row.row_index;
      });
      showDryRun(data);
    } else {
      var errData;
      try { errData = JSON.parse(xhr.responseText); } catch(e) { errData = {message: "Ошибка сервера"}; }
      showUploadError(errData.message || "Ошибка проверки файла");
      els.uploadControls.style.display = "block";
    }
  };

  xhr.onerror = function() {
    if (myGen !== gen) return;
    els.uploadBusy.style.display = "none";
    showUploadError("Сетевая ошибка");
    els.uploadControls.style.display = "block";
  };

  xhr.send(fd);
}

function showUploadError(msg) {
  els.uploadError.textContent = msg;
  els.uploadError.style.display = "block";
}

function showDryRun(data) {
  var s = data.summary;

  // Summary
  els.dryrunSummary.textContent = "";
  var sumDiv = document.createElement("div");
  sumDiv.className = "dryrun-summary-inner";
  sumDiv.textContent = "Профиль: " + data.profile.display_name +
    " | Всего: " + s.total_rows +
    " | Принято: " + s.accepted +
    " | Исключено: " + s.excluded;
  els.dryrunSummary.appendChild(sumDiv);

  // The actionable rows always come first.  Exclusions are rendered separately
  // at the bottom so a large diagnostic list never blocks the workflow.
  els.dryrunTables.textContent = "";
  if (data.accepted.length > 0) {
    var acTitle = document.createElement("h3");
    acTitle.textContent = "Доступно для вывода (" + data.accepted.length + ")";
    els.dryrunTables.appendChild(acTitle);

    var acTable = document.createElement("table");
    acTable.className = "dryrun-table";

    var acHead = document.createElement("thead");
    var acHeadRow = document.createElement("tr");
    var selectTh = document.createElement("th");
    selectTh.className = "kiz-checkbox-cell";
    selectTh.textContent = "✓";
    acHeadRow.appendChild(selectTh);
    var acThs = ["Строка", "КИ", "Чек (необяз.)", "ФН (необяз.)", "Сумма (коп)", "Дата"];
    for (var j = 0; j < acThs.length; j++) {
      var th = document.createElement("th");
      th.textContent = acThs[j];
      acHeadRow.appendChild(th);
    }
    acHead.appendChild(acHeadRow);
    acTable.appendChild(acHead);

    var acBody = document.createElement("tbody");
    for (var k = 0; k < data.accepted.length; k++) {
      var a = data.accepted[k];
      var acRow = document.createElement("tr");
      var selectTd = document.createElement("td");
      selectTd.className = "kiz-checkbox-cell";
      var checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "kiz-checkbox";
      checkbox.setAttribute("data-row-index", String(a.row_index));
      checkbox.setAttribute("aria-label", "Выбрать КИЗ из строки " + a.row_index);
      checkbox.checked = workspace(profileId).selectedRows.indexOf(a.row_index) !== -1;
      checkbox.addEventListener("change", updateSelection);
      selectTd.appendChild(checkbox);
      acRow.appendChild(selectTd);
      var fields = [String(a.row_index), a.ki, a.check_number || "—", a.fn_number || "—", String(a.cost_kopecks), a.date];
      for (var f = 0; f < fields.length; f++) {
        var td = document.createElement("td");
        td.textContent = fields[f];
        acRow.appendChild(td);
      }
      acBody.appendChild(acRow);
    }
    acTable.appendChild(acBody);
    els.dryrunTables.appendChild(acTable);
  }

  renderExcludedRows(data);
  els.submissionWorkspace.style.display = data.accepted.length > 0 ? "block" : "none";

  els.dryrunResults.style.display = "block";
  var today = new Date().toISOString().slice(0, 10);
  els.actionDate.value = today;
  els.documentDate.value = today;
  els.documentNumber.value = "WB-" + today.replace(/-/g, "") + "-" + data.import_token.slice(-6);
  updateSelection();
}

function renderExcludedRows(data) {
  els.excludedResults.textContent = "";
  if (data.excluded.length === 0) return;

  var details = document.createElement("details");
  details.className = "excluded-details";
  // When nothing can be submitted, show the reasons immediately.  Otherwise
  // keep hundreds of diagnostic rows collapsed until the user asks for them.
  details.open = data.accepted.length === 0;

  var summary = document.createElement("summary");
  var summaryTitle = document.createElement("span");
  summaryTitle.textContent = "Исключено (" + data.excluded.length + ")";
  var toggleLabel = document.createElement("span");
  toggleLabel.className = "excluded-toggle-label";
  summary.appendChild(summaryTitle);
  summary.appendChild(toggleLabel);
  details.appendChild(summary);

  var hint = document.createElement("p");
  hint.className = "excluded-hint";
  hint.textContent = "Эти строки не будут отправлены в Честный знак";
  details.appendChild(hint);

  var tableWrap = document.createElement("div");
  tableWrap.className = "excluded-table-wrap";
  var table = document.createElement("table");
  table.className = "dryrun-table excluded-table";

  var head = document.createElement("thead");
  var headRow = document.createElement("tr");
  var headings = ["Строка", "Причина", "Описание"];
  for (var h = 0; h < headings.length; h++) {
    var th = document.createElement("th");
    th.textContent = headings[h];
    headRow.appendChild(th);
  }
  head.appendChild(headRow);
  table.appendChild(head);

  var body = document.createElement("tbody");
  for (var i = 0; i < data.excluded.length; i++) {
    var excluded = data.excluded[i];
    var row = document.createElement("tr");
    var values = [String(excluded.row_index), excluded.reason_code, excluded.message];
    for (var j = 0; j < values.length; j++) {
      var td = document.createElement("td");
      td.textContent = values[j];
      row.appendChild(td);
    }
    body.appendChild(row);
  }
  table.appendChild(body);
  tableWrap.appendChild(table);
  details.appendChild(tableWrap);
  els.excludedResults.appendChild(details);
}

function setAllKizSelected(selected) {
  var checks = document.querySelectorAll(".kiz-checkbox");
  for (var i = 0; i < checks.length; i++) checks[i].checked = selected;
  updateSelection();
}

function updateSelection() {
  var ws = workspace(profileId);
  var checks = document.querySelectorAll(".kiz-checkbox");
  var selected = [];
  var selectedCost = 0;
  var costByRow = Object.create(null);
  if (ws.preview) {
    for (var i = 0; i < ws.preview.accepted.length; i++) {
      costByRow[ws.preview.accepted[i].row_index] = Number(ws.preview.accepted[i].cost_kopecks) || 0;
    }
  }
  for (var j = 0; j < checks.length; j++) {
    var rowIndex = Number(checks[j].getAttribute("data-row-index"));
    var row = checks[j].closest("tr");
    if (checks[j].checked) {
      selected.push(rowIndex);
      selectedCost += costByRow[rowIndex] || 0;
      if (row) row.classList.remove("not-selected");
    } else if (row) {
      row.classList.add("not-selected");
    }
  }
  ws.selectedRows = selected;
  els.selectAllKiz.checked = checks.length > 0 && selected.length === checks.length;
  els.selectAllKiz.indeterminate = selected.length > 0 && selected.length < checks.length;
  els.selectionSummary.textContent = "Выбрано: " + selected.length + " из " + checks.length +
    " · " + (selectedCost / 100).toLocaleString("ru-RU", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + " ₽";
  els.submitCzBtn.disabled = selected.length === 0;
  els.submitCzBtn.textContent = selected.length > 0
    ? "Вывести выбранные КИЗ (" + selected.length + ")"
    : "Выберите КИЗ для вывода";
}

function onSubmitCz() {
  if (activeImport === null) return;
  var ws = workspace(profileId);
  var selectedRows = ws.selectedRows.slice();
  if (selectedRows.length === 0) {
    showUploadError("Выберите хотя бы один КИЗ");
    return;
  }
  if (!els.actionDate.value || !els.documentDate.value || !els.documentNumber.value.trim()) {
    showUploadError("Заполните даты и номер первичного документа");
    return;
  }
  var selectedCost = 0;
  for (var i = 0; ws.preview && i < ws.preview.accepted.length; i++) {
    if (selectedRows.indexOf(ws.preview.accepted[i].row_index) !== -1) {
      selectedCost += Number(ws.preview.accepted[i].cost_kopecks) || 0;
    }
  }
  var profileName = ws.preview ? ws.preview.profile.display_name : profileId;
  var rubles = (selectedCost / 100).toLocaleString("ru-RU", {
    minimumFractionDigits: 2, maximumFractionDigits: 2
  });
  if (!confirm("Вывести из оборота " + selectedRows.length + " КИЗ на сумму " +
      rubles + " ₽ для " + profileName + "? Операцию нельзя отменить.")) return;

  var token = activeImport.token;
  els.submitCzBtn.disabled = true;
  els.cancelImportBtn.disabled = true;
  els.uploadBusy.style.display = "block";
  els.uploadBusy.textContent = "Подпись и отправка в Честный Знак…";

  fetch("/api/imports/" + encodeURIComponent(token) + "/submit", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      action_date: els.actionDate.value,
      document_date: els.documentDate.value,
      document_number: els.documentNumber.value.trim(),
      primary_document_custom_name: els.documentName.value.trim(),
      selected_rows: selectedRows
    })
  }).then(function(resp) {
    return resp.json().then(function(data) { return {ok: resp.ok, data: data}; });
  }).then(function(result) {
    els.uploadBusy.style.display = "none";
    if (result.data.remaining_import) {
      var remaining = result.data.remaining_import;
      activeImport = {token: remaining.import_token, profileId: profileId};
      ws.activeImport = activeImport;
      ws.preview = remaining;
      ws.selectedRows = remaining.accepted.map(function(row) { return row.row_index; });
      showDryRun(remaining);
      els.uploadControls.style.display = "none";
    }
    if (!result.ok) throw new Error(result.data.message || "Ошибка отправки");
    if (!result.data.remaining_import) {
      activeImport = null;
      ws.activeImport = null;
      ws.preview = null;
      ws.selectedRows = [];
    }
    var remainingCount = result.data.remaining_import
      ? result.data.remaining_import.summary.accepted : 0;
    showStatus("Отправлено: " + result.data.submitted +
      ", ошибок: " + result.data.failed + ", осталось в черновике: " + remainingCount);
    loadReport();
  }).catch(function(err) {
    els.uploadBusy.style.display = "none";
    els.submitCzBtn.disabled = false;
    els.cancelImportBtn.disabled = false;
    showUploadError(err.message || "Ошибка отправки");
  });
}

function onCancelImport() {
  if (activeImport === null) return;

  var myGen = gen;

  var xhr = new XMLHttpRequest();
  xhr.open("DELETE", "/api/imports/" + encodeURIComponent(activeImport.token), true);
  els.uploadBusy.style.display = "block";

  xhr.onload = function() {
    if (myGen !== gen) return;
    els.uploadBusy.style.display = "none";
    if (xhr.status === 204) {
      activeImport = null;
      workspace(profileId).activeImport = null;
      workspace(profileId).preview = null;
      workspace(profileId).selectedRows = [];
      els.dryrunResults.style.display = "none";
      els.uploadError.style.display = "none";
      els.fileInput.value = "";
      els.uploadControls.style.display = "block";
      showStatus("Импорт отменён");
    } else {
      showUploadError("Ошибка отмены импорта");
      els.uploadControls.style.display = "block";
    }
  };

  xhr.onerror = function() {
    if (myGen !== gen) return;
    els.uploadBusy.style.display = "none";
    showUploadError("Сетевая ошибка при отмене");
    els.uploadControls.style.display = "block";
  };

  xhr.send();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

// ── Report section ──────────────────────────────────────────────────────────

var reportGen = 0;

function loadReport() {
  var myGen = ++reportGen;
  var section = document.getElementById("report-section");
  var summary = document.getElementById("report-summary");
  if (section) section.style.display = "none";
  if (summary) summary.textContent = "";
  var xhr = new XMLHttpRequest();
  xhr.open("GET", "/api/packages/" + encodeURIComponent(profileId), true);

  xhr.onload = function() {
    if (myGen !== reportGen) return;
    if (xhr.status !== 200) return;

    var packages = JSON.parse(xhr.responseText);
    section = document.getElementById("report-section");
    summary = document.getElementById("report-summary");
    var btn = document.getElementById("show-report-btn");

    if (!section || !summary) return;

    var hasResults = false;
    var text = "";
    for (var i = 0; i < packages.length; i++) {
      var p = packages[i];
      if (p.status === "CONFIRMED" || p.status === "PARTIAL" || p.status === "FAILED") {
        hasResults = true;
        text += p.status + ": " + p.summary.accepted_submitted + " / " + p.summary.accepted + "\n";
      }
    }

    if (hasResults) {
      section.style.display = "block";
      summary.textContent = text;
      if (btn) {
        btn.onclick = function() {
          window.open("/api/packages/" + encodeURIComponent(profileId), "_blank");
        };
      }
    } else {
      section.style.display = "none";
    }
  };

  xhr.send();
}

// Hook into profile switch to reload report
document.addEventListener("profile-switched", loadReport);

})();
