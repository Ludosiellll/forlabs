// ForLabs: загрузка файлов, вставка скриншотов, живой журнал выполнения.
(function () {
  // выбор файлов методички
  document.querySelectorAll("[data-dropzone]").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]");
    var picked = zone.querySelector("[data-picked]");
    function show() {
      var names = Array.prototype.map.call(input.files, function (f) { return f.name; });
      picked.textContent = names.length ? "Выбрано: " + names.join(", ") : "";
    }
    input.addEventListener("change", show);
    zone.addEventListener("dragover", function (e) { e.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", function () { zone.classList.remove("over"); });
    zone.addEventListener("drop", function (e) {
      e.preventDefault(); zone.classList.remove("over");
      if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); }
    });
  });

  var lab = document.querySelector("[data-lab]");
  if (!lab) return;
  var base = lab.getAttribute("data-base");

  // переписать задание
  var redo = document.querySelector("[data-redo]");
  if (redo) {
    redo.addEventListener("submit", function () {
      redo.action = base + "/redo/" + redo.querySelector("[data-redo-select]").value;
    });
  }

  // слоты скриншотов
  var slots = Array.prototype.slice.call(document.querySelectorAll("[data-slot]"));
  function active() { return document.querySelector(".slot.on") || slots[0]; }
  function choose(slot) { slots.forEach(function (s) { s.classList.toggle("on", s === slot); }); }
  function upload(slot, blob) {
    if (!slot || !blob) return;
    slot.classList.add("busy");
    var info = slot.querySelector(".i");
    info.textContent = "Загружаю и пересобираю документ…";
    fetch(slot.getAttribute("data-slot"), { method: "POST", body: blob, headers: { "Content-Type": blob.type || "image/png" } })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok || res.j.error) { info.textContent = "Не получилось: " + (res.j.error || "ошибка"); slot.classList.remove("busy"); return; }
        location.href = base + "?tab=report";
      })
      .catch(function () { info.textContent = "Нет связи с приложением"; slot.classList.remove("busy"); });
  }
  slots.forEach(function (slot) {
    slot.addEventListener("click", function (e) { if (e.target.tagName !== "INPUT") choose(slot); });
    slot.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(slot); } });
    slot.addEventListener("dragover", function (e) { e.preventDefault(); choose(slot); });
    slot.addEventListener("drop", function (e) {
      e.preventDefault();
      var f = e.dataTransfer && e.dataTransfer.files[0];
      if (f && /^image\//.test(f.type)) upload(slot, f);
    });
    var input = slot.querySelector("[data-slot-file]");
    if (input) input.addEventListener("change", function () { if (input.files[0]) upload(slot, input.files[0]); });
  });
  document.addEventListener("paste", function (e) {
    if (!slots.length) return;
    var t = e.target;
    if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT")) return;
    var items = (e.clipboardData && e.clipboardData.items) || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].type.indexOf("image/") === 0) { e.preventDefault(); upload(active(), items[i].getAsFile()); return; }
    }
  });

  // живой журнал
  if (lab.getAttribute("data-busy") === "1") {
    var after = lab.getAttribute("data-seq") || "0";
    var journal = document.querySelector("[data-journal]");
    var live = document.querySelector("[data-live]");
    var es = new EventSource(base + "/events?after=" + after);
    es.onmessage = function (m) {
      var ev = JSON.parse(m.data);
      if (ev.kind === "moved") { es.close(); location.href = base.replace(/\/[^/]+$/, "/" + encodeURIComponent(ev.text)); return; }
      if (ev.kind === "idle") { es.close(); location.href = base; return; }
      if (ev.text && live) live.textContent = ev.text;
      if (ev.text && journal) {
        var li = document.createElement("li"); li.className = "k-" + ev.kind; li.textContent = ev.text; journal.appendChild(li);
      }
      if (ev.step) {
        document.querySelectorAll("[data-step]").forEach(function (li) {
          if (li.getAttribute("data-step") === ev.step && !li.classList.contains("done")) li.className = "now";
        });
      }
    };
    es.onerror = function () { es.close(); setTimeout(function () { location.reload(); }, 3000); };
  }
})();

// админ-панель: остатки на ключах (подгружаются после открытия страницы), подсказки графиков, строки-ссылки
(function () {
  var root = document.querySelector("[data-balances]");
  if (root && document.querySelector("[data-balance]")) {
    fetch(root.getAttribute("data-balances")).then(function (r) { return r.json(); }).then(function (data) {
      document.querySelectorAll("[data-balance]").forEach(function (el) {
        var d = data[el.getAttribute("data-balance")];
        el.textContent = d ? d.text : "—";
        if (d && d.labs) el.title = d.labs;
      });
      document.querySelectorAll("[data-balance-labs]").forEach(function (el) {
        var d = data[el.getAttribute("data-balance-labs")];
        if (d && d.labs) el.textContent = d.labs;
      });
    }).catch(function () {
      document.querySelectorAll("[data-balance]").forEach(function (el) { el.textContent = "нет связи"; });
    });
  }
  var tip = null;
  function hide() { if (tip) { tip.remove(); tip = null; } }
  document.addEventListener("mousemove", function (e) {
    var t = e.target.closest && e.target.closest(".chart [data-tip]");
    if (!t) { hide(); return; }
    if (!tip) { tip = document.createElement("div"); tip.className = "viz-tip"; document.body.appendChild(tip); }
    tip.textContent = t.getAttribute("data-tip");
    var x = e.clientX + 14, y = e.clientY + 14, w = tip.offsetWidth, h = tip.offsetHeight;
    if (x + w > window.innerWidth - 8) x = e.clientX - w - 14;
    if (y + h > window.innerHeight - 8) y = e.clientY - h - 14;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  });
  document.addEventListener("mouseleave", hide);
  document.querySelectorAll("tr.click[data-href]").forEach(function (tr) {
    tr.addEventListener("click", function (e) { if (!e.target.closest("a, button, input")) location.href = tr.getAttribute("data-href"); });
  });
})();

// чат правок: прокрутка к последнему сообщению, Ctrl+Enter — отправить
(function () {
  var log = document.querySelector("[data-chat-log]");
  if (log) log.scrollTop = log.scrollHeight;
  var form = document.querySelector("[data-chat-form]");
  if (!form) return;
  var area = form.querySelector("textarea");
  var files = form.querySelector("[data-images-input]");
  area.addEventListener("keydown", function (e) {
    var hasPhotos = files && files.files && files.files.length;
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && (area.value.trim() || hasPhotos)) {
      e.preventDefault();
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    }
  });
  form.addEventListener("submit", function () {
    var b = form.querySelector("button[type=submit]");
    if (b) { b.disabled = true; b.textContent = "Отправляю…"; }
  });
})();

// фото: кнопка «Прикрепить фото» и вставка картинок из буфера (Ctrl+V) в поле текста
(function () {
  function picked(input) {
    var out = document.querySelector('[data-images-picked="' + input.id + '"]');
    if (!out) return;
    var n = input.files ? input.files.length : 0;
    var photos = /^image\//.test(input.getAttribute("accept") || "");
    out.textContent = !n ? "" : photos ? ("Фото: " + n + (n > 4 ? ", можно не больше 4" : ""))
      : Array.prototype.map.call(input.files, function (f) { return f.name; }).join(", ");
  }
  document.querySelectorAll("[data-images-input]").forEach(function (input) {
    input.addEventListener("change", function () { picked(input); });
  });
  document.querySelectorAll("[data-autosubmit]").forEach(function (input) { // файл выбран — сразу загрузить
    input.addEventListener("change", function () {
      if (!input.files || !input.files.length) return;
      var label = input.closest("label");
      if (label && label.firstChild) label.firstChild.textContent = "Загружаю…";
      if (input.form.requestSubmit) input.form.requestSubmit(); else input.form.submit();
    });
  });
  document.querySelectorAll("[data-paste-images]").forEach(function (area) {
    var input = document.getElementById(area.getAttribute("data-paste-images"));
    if (!input || typeof DataTransfer === "undefined") return;
    area.addEventListener("paste", function (e) {
      var items = (e.clipboardData && e.clipboardData.files) || [];
      var images = Array.prototype.filter.call(items, function (f) { return /^image\//.test(f.type); });
      if (!images.length) return;
      e.preventDefault();
      var dt = new DataTransfer();
      Array.prototype.forEach.call(input.files || [], function (f) { dt.items.add(f); });
      images.forEach(function (f, i) {
        dt.items.add(new File([f], "фото-" + Date.now() + "-" + i + ".png", { type: f.type }));
      });
      input.files = dt.files;
      picked(input);
    });
  });
})();

// любая отправка формы (form[method=post]): кнопка отключается — двойной клик не запускает действие дважды,
// а долгое действие («Сдать», отчёт за семестр) видно сразу. Возврат кнопкой «Назад» включает кнопки обратно.
document.querySelectorAll("form[method=post], form[method=POST]").forEach(function (form) {
  form.addEventListener("submit", function (ev) {
    if (ev.defaultPrevented) { return; }
    var b = ev.submitter || form.querySelector("button[type=submit]");
    if (b && !b.disabled) {
      b.dataset.label = b.textContent;
      setTimeout(function () { b.disabled = true; b.textContent = "Подожди…"; }, 0);
    }
  });
});
window.addEventListener("pageshow", function () {
  document.querySelectorAll("form button[data-label]").forEach(function (b) {
    b.disabled = false; b.textContent = b.dataset.label; delete b.dataset.label;
  });
});

// поддержка: пока страница открыта, новые сообщения подгружаются сами (набранный текст не трогается)
(function () {
  var box = document.querySelector("[data-support-refresh]");
  if (!box) return;
  var url = box.getAttribute("data-support-refresh");
  function tick() {
    if (document.visibilityState !== "visible") return;
    fetch(url, { credentials: "same-origin" }).then(function (r) { return r.ok ? r.text() : null; }).then(function (html) {
      if (!html) return;
      var cur = box.querySelector("[data-chat-log]");
      var tmp = document.createElement("div");
      tmp.innerHTML = html;
      var next = tmp.querySelector("[data-chat-log]");
      if (!cur || !next || cur.getAttribute("data-count") === next.getAttribute("data-count")) return;
      cur.replaceWith(next);
      next.scrollTop = next.scrollHeight;
    }).catch(function () {});
  }
  setInterval(tick, 10000);
})();

// обновление из приложения: карточка в углу «Доступно обновление» → «Обновить» → загрузка с полосой →
// установщик ставит новую версию, ForLabs закрывается и открывается уже обновлённым
(function () {
  var box = document.querySelector("[data-update]");
  if (!box) return;
  var title = box.querySelector("[data-update-title]"), text = box.querySelector("[data-update-text]"),
      go = box.querySelector("[data-update-go]"), actions = box.querySelector("[data-update-actions]"),
      track = box.querySelector("[data-update-track]"), bar = box.querySelector("[data-update-bar]");
  var version = box.getAttribute("data-version");
  function open() { if (box.hidden) { box.hidden = false; box.classList.add("in"); } }
  function show(s) {
    if (s.version) version = s.version;
    box.setAttribute("data-state", s.state);
    track.hidden = s.state !== "downloading";
    actions.hidden = s.state === "downloading" || s.state === "installing" || s.state === "ready";
    box.querySelector(".update-x").hidden = !(s.state === "available" || s.state === "error"); // начатое не прячется
    if (s.state === "available") {
      title.textContent = "Доступно обновление";
      text.textContent = "ForLabs " + version + (s.size_mb ? " · " + s.size_mb + " МБ" : "") +
        ". Лабы, профили и настройки останутся.";
      go.textContent = "Обновить"; go.disabled = false;
    } else if (s.state === "downloading") {
      title.textContent = "Скачиваю ForLabs " + version;
      bar.style.width = s.percent + "%";
      text.textContent = s.note || (s.percent + "% · можно продолжать работу");
    } else if (s.state === "ready") {
      title.textContent = "Обновление скачано";
      text.textContent = "Устанавливаю…";
    } else if (s.state === "error") {
      title.textContent = "Обновление не удалось";
      text.textContent = (s.error || "Что-то пошло не так.").replace(/^./, function (c) { return c.toUpperCase(); });
      go.textContent = "Повторить"; go.disabled = false;
    }
  }
  function post(url) {
    return fetch(url, { method: "POST", credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (j) { j.httpStatus = r.status; return j; }); });
  }
  function install() {
    post("/update/install").then(function (r) {
      if (r.ok) {
        title.textContent = "Перезапускаю ForLabs";
        text.textContent = "Окно закроется, и примерно через минуту откроется ForLabs " + version + ".";
        actions.hidden = true; track.hidden = true;
      } else if (r.httpStatus === 409 && /лаб/.test(r.error || "")) {
        title.textContent = "Обновление скачано";
        text.textContent = r.error; // «Сейчас выполняется лаба. Обновлю, когда она закончится.»
        setTimeout(install, 5000);
      } else {
        show({ state: "error", error: r.error });
      }
    }).catch(function () { setTimeout(install, 3000); }); // ForLabs уже закрывается — это нормально
  }
  function poll() {
    fetch("/update/status", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (s) {
      show(s);
      if (s.state === "downloading") setTimeout(poll, 400);
      else if (s.state === "ready") install();
    }).catch(function () { setTimeout(poll, 2000); });
  }
  go.addEventListener("click", function () {
    go.disabled = true;
    post("/update/start").then(function (s) { show(s); poll(); })
      .catch(function () { show({ state: "error", error: "ForLabs не ответил, нажми ещё раз." }); });
  });
  box.querySelectorAll("[data-update-later]").forEach(function (b) {
    b.addEventListener("click", function () {
      box.hidden = true;
      post("/update/later").catch(function () {});
    });
  });
  var st = box.getAttribute("data-state");
  if (!box.hidden && (st === "downloading" || st === "ready")) poll(); // загрузка идёт (переход на другую страницу)
  if (box.hidden) { // проверка при запуске идёт в фоне — карточка появится, когда найдёт новую версию
    [2000, 7000, 20000].forEach(function (ms) {
      setTimeout(function () {
        if (!box.hidden) return;
        fetch("/update/status", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (s) {
          if (!s.dismissed && (s.state === "available" || s.state === "downloading" || s.state === "ready")) {
            show(s); open();
            if (s.state !== "available") poll();
          }
        }).catch(function () {});
      }, ms);
    });
  }
})();
