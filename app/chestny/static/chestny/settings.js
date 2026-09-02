(function() {
"use strict";

var profileId = "org-sinyavin";
var gen = 0;
var abort = null;

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

  loadProfile(profileId);
}

function setBusy(busy) {
  for (var i = 0; i < btnEls.length; i++) {
    btnEls[i].disabled = busy;
  }
  for (var j = 0; j < tabEls.length; j++) {
    tabEls[j].disabled = busy;
  }
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

function onTabClick(e) {
  var btn = e.currentTarget;
  var pid = btn.getAttribute("data-profile");
  if (pid === profileId) return;

  for (var i = 0; i < tabEls.length; i++) {
    tabEls[i].classList.remove("active");
    tabEls[i].setAttribute("aria-selected", "false");
  }
  btn.classList.add("active");
  btn.setAttribute("aria-selected", "true");

  profileId = pid;
  loadProfile(pid);
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
        savedOpt.textContent = "\u0421\u043e\u0445\u0440\u0430\u043d\u0451\u043d: \u2026" + tp.slice(-8);
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
      emptyOpt.textContent = "\u2014 выберите сертификат \u2014";
      els.cert.appendChild(emptyOpt);

      var found = false;
      for (var i = 0; i < certs.length; i++) {
        var c = certs[i];
        var opt = document.createElement("option");
        opt.value = c.thumbprint;
        var label = (c.subject || "") + " / " + (c.store || "") + " \u2026" + (c.thumbprint ? c.thumbprint.slice(-8) : "");
        opt.textContent = label;
        els.cert.appendChild(opt);
        if (c.thumbprint === currentVal) found = true;
      }

      if (currentVal && !found) {
        var missingOpt = document.createElement("option");
        missingOpt.value = currentVal;
        missingOpt.textContent = "\u2605 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d: \u2026" + currentVal.slice(-8);
        els.cert.appendChild(missingOpt);
        els.cert.value = currentVal;
      } else if (currentVal && found) {
        els.cert.value = currentVal;
      }

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
      showStatus(parts.join(" | "));
      done(myGen);
    })
    .catch(function() {
      if (myGen !== gen) return;
      showError("Ошибка проверки сертификата");
      done(myGen);
    });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

})();
