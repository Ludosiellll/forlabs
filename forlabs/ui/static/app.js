// ForLabs: загрузка файлов, вставка скриншотов, живой журнал выполнения.
(function () {
  // выбор файлов методички: список с крестиком у каждого файла и «Убрать все». Файлы из окна выбора и перетащенные
  // добавляются к уже выбранным; в поле формы список собирается заново через DataTransfer — уходит ровно то, что видно
  document.querySelectorAll("[data-dropzone]").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]");
    var picked = zone.querySelector("[data-picked]");
    var many = zone.querySelector("[data-picked-many]");
    var slice = function (list) { return Array.prototype.slice.call(list || []); };
    var editable = false;
    try { editable = !!new DataTransfer().items; } catch (err) { editable = false; }
    var accept = (input.getAttribute("accept") || "").toLowerCase().split(",")
      .map(function (s) { return s.trim(); }).filter(Boolean);
    var files = slice(input.files);
    var note = document.createElement("div"); note.className = "pick-note"; note.hidden = true;
    var live = document.createElement("span"); live.className = "visually-hidden"; live.setAttribute("aria-live", "polite");
    picked.after(note, live);

    function fits(f) {
      var name = f.name.toLowerCase();
      return !accept.length || accept.some(function (a) { return a.charAt(0) === "." && name.slice(-a.length) === a; });
    }
    function size(n) {
      return n < 1048576 ? Math.max(1, Math.round(n / 1024)) + " КБ" : (n / 1048576).toFixed(1).replace(".", ",") + " МБ";
    }
    function sync() {
      if (!editable) return;
      var dt = new DataTransfer();
      files.forEach(function (f) { dt.items.add(f); });
      input.files = dt.files;
    }
    function render(focusAt) {
      picked.textContent = "";
      if (many) many.hidden = files.length < 2;
      if (!files.length) return;
      var ul = document.createElement("ul"); ul.className = "picked-list";
      files.forEach(function (f, i) {
        var li = document.createElement("li");
        var name = document.createElement("span"); name.className = "pick-name"; name.textContent = f.name;
        var weight = document.createElement("span"); weight.className = "pick-size"; weight.textContent = size(f.size);
        li.append(name, weight);
        if (editable) {
          var x = document.createElement("button");
          x.type = "button"; x.className = "pick-x"; x.textContent = "✕"; x.title = "Убрать";
          x.setAttribute("aria-label", "Убрать «" + f.name + "»");
          x.addEventListener("click", function () { remove(i); });
          li.append(x);
        }
        ul.append(li);
      });
      picked.append(ul);
      if (files.length > 1 || !editable) {
        var all = document.createElement("button");
        all.type = "button"; all.className = "btn link small pick-all"; all.textContent = "Убрать все";
        all.addEventListener("click", clear);
        picked.append(all);
      }
      if (focusAt != null) {
        var xs = picked.querySelectorAll(".pick-x");
        if (xs.length) xs[Math.min(focusAt, xs.length - 1)].focus();
      }
    }
    function remove(i) {
      var gone = files.splice(i, 1)[0];
      sync();
      live.textContent = "Убран «" + gone.name + "»";
      render(i);
      if (!files.length) input.focus();
    }
    function clear() {
      files = [];
      if (editable) sync(); else input.value = "";
      note.hidden = true; live.textContent = "Выбор очищен";
      render();
      input.focus();
    }
    function add(list) {
      var wrong = [];
      slice(list).forEach(function (f) {
        if (!fits(f)) { wrong.push(f.name); return; }
        var same = files.findIndex(function (g) { return g.name.toLowerCase() === f.name.toLowerCase(); });
        if (same >= 0) files[same] = f; else files.push(f); // тот же файл ещё раз — заменяет прежний
      });
      var doc = wrong.filter(function (n) { return /\.doc$/i.test(n); });
      note.textContent = !wrong.length ? "" : doc.length === wrong.length
        ? "«" + wrong.join("», «") + "» — старый формат Word: открой в Word и сохрани как .docx."
        : "Не добавлено: «" + wrong.join("», «") + "». Подходят PDF, DOCX, TXT и фото страниц.";
      note.hidden = !wrong.length;
      sync();
      render();
    }
    input.addEventListener("change", function () {
      if (editable) { add(input.files); return; }
      files = slice(input.files); render();
    });
    zone.addEventListener("dragover", function (e) { e.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", function () { zone.classList.remove("over"); });
    zone.addEventListener("drop", function (e) {
      e.preventDefault(); zone.classList.remove("over");
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      if (editable) { add(e.dataTransfer.files); return; }
      input.files = e.dataTransfer.files; files = slice(input.files); render();
    });
    render();
  });

  var lab = document.querySelector("[data-lab]");
  if (!lab) return;
  var base = lab.getAttribute("data-base");

  // переписать задание
  var redo = document.querySelector("[data-redo]");
  if (redo) {
    redo.addEventListener("submit", function () {
      redo.action = base + "/redo/" + document.querySelector("[data-redo-select]").value;
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
    var shotsPanel = document.querySelector('[data-panel="shots"]');
    if (shotsPanel && shotsPanel.hidden) return;  // открыт чат или замечания: вставка не уходит в скрытый рисунок
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

// переход по вкладке виден сразу: вкладка подсвечивается и сверху бежит полоска, пока страница грузится
(function () {
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest(".nav a, a[data-go]");
    if (!a || e.defaultPrevented || e.button || e.ctrlKey || e.metaKey || e.shiftKey || a.target === "_blank") return;
    document.querySelectorAll(".nav a.on").forEach(function (x) { x.classList.remove("on"); });
    if (a.closest(".nav")) a.classList.add("on");
    document.documentElement.classList.add("nav-going");
  });
  window.addEventListener("pageshow", function () { document.documentElement.classList.remove("nav-going"); });
})();

// «Настройки»: остаток на ключе подгружается после показа страницы
(function () {
  var el = document.querySelector("[data-my-balance]");
  if (!el) return;
  fetch(el.getAttribute("data-my-balance")).then(function (r) { return r.json(); }).then(function (d) {
    el.textContent = d.text || "Остаток сейчас не узнать.";
    el.classList.toggle("muted", d.state !== "ok");
  }).catch(function () { el.textContent = "Остаток сейчас не узнать: нет связи."; });
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

// вкладки правой колонки лабы: выбранная запоминается для этой лабы до закрытия окна
(function () {
  var bar = document.querySelector("[data-side-tabs]");
  function show(name) {
    var panels = document.querySelectorAll("[data-panel]"), found = false;
    panels.forEach(function (p) { if (p.getAttribute("data-panel") === name) found = true; });
    if (!found) return;
    panels.forEach(function (p) { p.hidden = p.getAttribute("data-panel") !== name; });
    document.querySelectorAll("[data-side]").forEach(function (b) {
      b.setAttribute("aria-selected", b.getAttribute("data-side") === name ? "true" : "false");
    });
    var log = document.querySelector("[data-chat-log]");
    if (name === "chat" && log) log.scrollTop = log.scrollHeight;
  }
  var key = bar ? "forlabs-side:" + bar.getAttribute("data-side-tabs") : null;
  function remember(name) { try { if (key) sessionStorage.setItem(key, name); } catch (e) { /* нет хранилища */ } }
  if (bar) {
    var saved = null;
    try { saved = sessionStorage.getItem(key); } catch (e) { /* нет хранилища */ }
    if (saved) show(saved);
    bar.addEventListener("click", function (e) {
      var b = e.target.closest("[data-side]");
      if (b) { show(b.getAttribute("data-side")); remember(b.getAttribute("data-side")); }
    });
  }
  document.querySelectorAll("[data-side-go]").forEach(function (a) {
    a.addEventListener("click", function (e) { e.preventDefault(); show(a.getAttribute("data-side-go")); });
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
  function grow() { area.style.height = "auto"; area.style.height = Math.min(area.scrollHeight, 160) + "px"; }
  area.addEventListener("input", grow);  // поле растёт вместе с текстом, как в мессенджере
  document.addEventListener("click", function (e) {  // меню «Ещё» закрывается щелчком мимо
    document.querySelectorAll("details.more[open]").forEach(function (d) { if (!d.contains(e.target)) d.open = false; });
  });
  area.addEventListener("keydown", function (e) {
    var hasPhotos = files && files.files && files.files.length;
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && (area.value.trim() || hasPhotos)) {
      e.preventDefault();
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    }
  });
  form.addEventListener("submit", function () {
    var b = form.querySelector("[data-send]") || form.querySelector("button[type=submit]");
    if (b) { b.disabled = true; b.textContent = "Отправляю…"; }
    var bar = document.querySelector("[data-side-tabs]");  // после отправки страница откроется на чате
    try { if (bar) sessionStorage.setItem("forlabs-side:" + bar.getAttribute("data-side-tabs"), "chat"); } catch (e) { /* нет хранилища */ }
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

// проверка Telegram-бота в «Админ → Поддержка»: по пунктам, а после ссылки ждёт «Запустить» и сам обновляет страницу
(function () {
  var box = document.querySelector("[data-tg-check]");
  if (!box) return;
  var wasBound = box.getAttribute("data-bound") === "1", tries = 0;
  function load() {
    fetch(box.getAttribute("data-tg-check"), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        box.innerHTML = "";
        (d.items || []).forEach(function (i) {
          var li = document.createElement("li");
          li.className = i.ok ? "ok" : "bad";
          li.textContent = (i.ok ? "✓ " : "✗ ") + i.text;
          box.appendChild(li);
        });
        if (d.bound && !wasBound) { location.reload(); return; }
        if (!d.bound && box.hasAttribute("data-wait") && ++tries < 200) setTimeout(load, 3000);
      })
      .catch(function () { if (box.hasAttribute("data-wait") && ++tries < 200) setTimeout(load, 5000); });
  }
  load();
})();

// программы для лаб (Настройки и блок «нет программы» на странице лабы): «Скачать и установить» с полосой,
// «У меня есть ссылка» — сначала сайт и размер, потом загрузка в «Загрузки» и открытие файла
(function () {
  var rows = Array.prototype.slice.call(document.querySelectorAll("[data-tool]"));
  if (!rows.length) return;
  var timer = null, linkRow = null, vcredist = null, first = true;
  function q(row, sel) { return row.querySelector(sel); }
  function busy(st) { return st === "downloading" || st === "installing"; }
  function watched(row) { return row.getAttribute("data-watch") || row.getAttribute("data-tool"); }
  function post(url, data) {
    var body = new FormData();
    Object.keys(data || {}).forEach(function (k) { body.append(k, data[k]); });
    return fetch(url, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { return r.json(); });
  }
  function button(text, primary, onClick) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "btn small" + (primary ? " primary" : ""); b.textContent = text;
    b.addEventListener("click", onClick);
    return b;
  }
  function extra(row, buttons) {
    var box = q(row, "[data-tool-extra]");
    box.innerHTML = "";
    (buttons || []).forEach(function (b) { box.appendChild(b); });
    box.hidden = !box.children.length;
  }
  function say(row, text, err) {
    var m = q(row, "[data-tool-msg]");
    m.textContent = text || "";
    m.classList.toggle("err", !!err);
  }
  function bar(row, on, percent) {
    q(row, "[data-tool-track]").hidden = !on;
    q(row, "[data-tool-bar]").style.width = (percent == null ? 3 : percent) + "%";
  }
  function showTool(row, t) {
    var st = t.state, missing = row.hasAttribute("data-missing");
    row.setAttribute("data-state", st);
    bar(row, busy(st), st === "installing" ? 100 : t.percent);
    var found = q(row, "[data-tool-found]");
    if (found && t.key === row.getAttribute("data-tool")) {
      found.textContent = t.found ? "есть" : "нет"; found.classList.toggle("yes", t.found);
      var path = q(row, "[data-tool-path]");
      path.textContent = [t.version, t.path].filter(Boolean).join(" · ");
      path.hidden = !t.found;
    }
    q(row, "[data-tool-act]").hidden = busy(st) || st === "done" || (t.found && !missing && st !== "error");
    if (st === "downloading") {
      say(row, t.note_now || ("Скачиваю " + t.name + "… " + (t.percent != null ? t.percent + "%" : t.done_mb + " МБ")));
    } else if (st === "installing") {
      say(row, t.note_now || "Устанавливаю…");
    } else if (st === "done") {
      say(row, missing ? "Готово — нажми «Повторить»." : "Готово" + (t.installed_version ? ": " + t.installed_version : "") + ".");
    } else if (st === "error") {
      say(row, t.error, true);
      var b = row.querySelector('[data-tool-install="' + t.key + '"]');
      if (b) b.textContent = "Повторить";
    }
    if (st === "error" && t.help === "vcredist" && vcredist) {
      extra(row, [button("Скачать Visual C++ (≈" + vcredist.size_mb + " МБ)", true, function () {
        check(row, vcredist.url, "");
      })]);
    }
  }
  function mb(x) { return x != null ? x + " МБ" : "размер неизвестен"; }
  function showLink(row, l) {
    if (l.state === "ready") {
      var where = l.host + (l.final_host && l.final_host !== l.host ? " → " + l.final_host : "");
      say(row, "Сайт: " + where + " · " + l.filename + " · " + mb(l.size_mb));
      extra(row, [button("Скачать", true, function () {
        extra(row, []);
        q(row, "[data-tool-link]").hidden = true;
        post("/tools/link", { url: l.url, key: l.key, go: "1" }).then(function (r) {
          if (!r.ok) { say(row, r.error, true); return; }
          poll();
        });
      }), button("Отмена", false, function () { say(row, ""); extra(row, []); linkRow = null; })]);
    } else if (l.state === "downloading") {
      bar(row, true, l.percent);
      say(row, l.note || ("Скачиваю " + l.filename + "… " + (l.percent != null ? l.percent + "%" : l.done_mb + " МБ")));
      extra(row, []);
    } else if (l.state === "done") {
      bar(row, false);
      var again = row.hasAttribute("data-missing") ? "«Повторить»" : "«Проверить снова»";
      if (l.unpack) {
        say(row, "Скачано: " + l.filename + ". Это архив " + l.unpack_name + ".");
        extra(row, [button("Распаковать для ForLabs", true, function () {
          row.setAttribute("data-watch", l.unpack); linkRow = null; extra(row, []);
          post("/tools/link/unpack").then(function (r) { if (!r.ok) say(row, r.error, true); poll(); });
        }), button("Открыть", false, function () { post("/tools/link/open"); })]);
      } else {
        say(row, "Скачано в «Загрузки»: " + l.filename + ". " +
          (l.opened ? "Файл открыт — установи программу и нажми " + again + "." : (l.note || "")));
        extra(row, [button("Открыть ещё раз", false, function () { post("/tools/link/open"); })]);
      }
    } else if (l.state === "error") {
      bar(row, false);
      say(row, l.error, true);
      extra(row, []);
    }
  }
  function poll(refresh) {
    clearTimeout(timer);
    fetch("/tools/status" + (refresh ? "?refresh=1" : ""), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        vcredist = s.vcredist || vcredist;
        rows.forEach(function (row) {
          var t = s.tools[watched(row)];
          if (t && (t.state !== "idle" || refresh || row !== linkRow)) showTool(row, t);
        });
        if (first && s.link.key && s.link.state !== "idle" && s.link.state !== "ready") { // после перехода
          linkRow = document.querySelector('[data-tool="' + s.link.key + '"]');               // со страницы
        }
        first = false;
        if (linkRow && s.link.state !== "idle") showLink(linkRow, s.link);
        var going = s.link.state === "downloading" || rows.some(function (row) {
          var t = s.tools[watched(row)];
          return t && busy(t.state);
        });
        if (going) timer = setTimeout(poll, 500);
      })
      .catch(function () { timer = setTimeout(poll, 2000); });
  }
  function check(row, url, key) {
    say(row, "Проверяю ссылку…");
    linkRow = row;
    post("/tools/link", { url: url, key: key }).then(function (r) {
      if (!r.ok) { say(row, r.error, true); return; }
      showLink(row, r.link);
    }).catch(function () { say(row, "ForLabs не ответил — попробуй ещё раз.", true); });
  }
  rows.forEach(function (row) {
    row.querySelectorAll("[data-tool-install]").forEach(function (b) {
      b.addEventListener("click", function () {
        row.setAttribute("data-watch", b.getAttribute("data-tool-install"));
        if (linkRow === row) linkRow = null;
        extra(row, []);
        say(row, "Начинаю…");
        q(row, "[data-tool-act]").hidden = true;
        post("/tools/" + encodeURIComponent(watched(row)) + "/install").then(function (r) {
          if (!r.ok) { say(row, r.error, true); q(row, "[data-tool-act]").hidden = false; return; }
          poll();
        }).catch(function () { setTimeout(poll, 1000); });
      });
    });
    var form = q(row, "[data-tool-link]");
    q(row, "[data-tool-link-show]").addEventListener("click", function () {
      form.hidden = !form.hidden;
      if (!form.hidden) form.querySelector("input[name=url]").focus();
    });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      check(row, form.querySelector("input[name=url]").value, form.querySelector("input[name=key]").value);
    });
  });
  var recheck = document.querySelector("[data-tools-recheck]");
  if (recheck) recheck.addEventListener("click", function () { poll(true); });
  poll(); // загрузка могла идти, пока студент был на другой странице
})();
