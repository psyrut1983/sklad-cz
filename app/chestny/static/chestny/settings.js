(function() {
"use strict";

var profileId = "org-sinyavin";
var gen = 0;
var abort = null;
var certOk = false;
var activeImport = null;   // {token, profileId}

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
  els.cancelImportBtn = document.getElementById("cancel-import-btn");
  els.submitCzBtn = document.getElementById("submit-cz-btn");

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

  els.cert.addEventListener("change", function() {
    certOk = false;
    updateGate();
  });

  loadProfile(profileId);
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

  if (!confirmIfActiveImport()) return;

  var self = this;
  cancelActiveImport(function(ok) {
    if (!ok) return;
    for (var i = 0; i < tabEls.length; i++) {
      tabEls[i].classList.remove("active");
      tabEls[i].setAttribute("aria-selected", "false");
    }
    btn.classList.add("active");
    btn.setAttribute("aria-selected", "true");

    profileId = pid;
    certOk = false;
    clearUpload();
    updateGate();
    loadProfile(pid);
  });
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
      } else {
        parts.push("Найден: нет");
      }

      if (diag.configured && diag.found && diag.has_private_key) {
        certOk = true;
        updateGate();
      } else {
        certOk = false;
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

  // Tables
  els.dryrunTables.textContent = "";

  // Excluded table
  if (data.excluded.length > 0) {
    var exTitle = document.createElement("h3");
    exTitle.textContent = "Исключено (" + data.excluded.length + ")";
    els.dryrunTables.appendChild(exTitle);

    var exTable = document.createElement("table");
    exTable.className = "dryrun-table";

    var exHead = document.createElement("thead");
    var exHeadRow = document.createElement("tr");
    var exTh1 = document.createElement("th");
    exTh1.textContent = "Строка";
    var exTh2 = document.createElement("th");
    exTh2.textContent = "Причина";
    var exTh3 = document.createElement("th");
    exTh3.textContent = "Описание";
    exHeadRow.appendChild(exTh1);
    exHeadRow.appendChild(exTh2);
    exHeadRow.appendChild(exTh3);
    exHead.appendChild(exHeadRow);
    exTable.appendChild(exHead);

    var exBody = document.createElement("tbody");
    for (var i = 0; i < data.excluded.length; i++) {
      var ex = data.excluded[i];
      var exRow = document.createElement("tr");
      var exTd1 = document.createElement("td");
      exTd1.textContent = String(ex.row_index);
      var exTd2 = document.createElement("td");
      exTd2.textContent = ex.reason_code;
      var exTd3 = document.createElement("td");
      exTd3.textContent = ex.message;
      exRow.appendChild(exTd1);
      exRow.appendChild(exTd2);
      exRow.appendChild(exTd3);
      exBody.appendChild(exRow);
    }
    exTable.appendChild(exBody);
    els.dryrunTables.appendChild(exTable);
  }

  // Accepted table
  if (data.accepted.length > 0) {
    var acTitle = document.createElement("h3");
    acTitle.textContent = "Принято (" + data.accepted.length + ")";
    els.dryrunTables.appendChild(acTitle);

    var acTable = document.createElement("table");
    acTable.className = "dryrun-table";

    var acHead = document.createElement("thead");
    var acHeadRow = document.createElement("tr");
    var acThs = ["Строка", "КИ", "Чек", "ФН", "Сумма (коп)", "Дата"];
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
      var fields = [String(a.row_index), a.ki, a.check_number, a.fn_number, String(a.cost_kopecks), a.date];
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

  els.dryrunResults.style.display = "block";
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
  var xhr = new XMLHttpRequest();
  xhr.open("GET", "/api/packages/" + encodeURIComponent(profileId), true);

  xhr.onload = function() {
    if (myGen !== reportGen) return;
    if (xhr.status !== 200) return;

    var packages = JSON.parse(xhr.responseText);
    var section = document.getElementById("report-section");
    var summary = document.getElementById("report-summary");
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
