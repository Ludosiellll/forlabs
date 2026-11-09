// ForLabs: обучение при первом запуске. Подсветка настоящих кнопок и карточки шагов.
// Движение — токены Fluent 2: карточка появляется как Scale-presence (250 мс, decelerateMax),
// подсветка переезжает к следующей кнопке за 400 мс по cubic-bezier(0.1, 0.9, 0.2, 1).
(function () {
  "use strict";
  var startBtn = document.querySelector('[data-tour="start"]');
  var startIsNewLab = startBtn && /Новая лаба/.test(startBtn.textContent);
  var STEPS = [
    { title: "Добро пожаловать в ForLabs",
      text: "За минуту покажу, где что находится. Можно пропустить: обучение всегда есть в «Настройках»." },
    { at: "profiles", title: "Профили",
      text: "Сначала профиль: ФИО, группа, предметы, варианты и руководители. Из него собираются титульный лист и шапка отчёта." },
    startIsNewLab
      ? { at: "start", title: "Новая лаба",
          text: "Загрузи методичку: PDF, Word или фото. ForLabs разберёт задания, выполнит их и соберёт отчёт .docx по оформлению колледжа." }
      : { at: "start", title: "С этого и начни",
          text: "Создай профиль. После этого здесь появится кнопка «Новая лаба» — туда загружаются методички." },
    { at: "labs", title: "Лабы",
      text: "Все лабы по предметам и их состояние. Внутри лабы — предпросмотр отчёта и чат «Правки»: напиши замечание преподавателя своими словами, и ForLabs исправит отчёт." },
    { at: "semester", title: "Отчёт за семестр",
      text: "Готовые лабы одного предмета — одним документом с титулом и содержанием." },
    { at: "rules", title: "Правила",
      text: "Как писать и оформлять: стиль, требования колледжа, особенности предметов. Можно подправить под своего преподавателя." },
    { at: "support", title: "Поддержка",
      text: "Если что-то не получается или нужно продлить доступ — напиши сюда, администратор ответит в этом же окне." },
    { at: "settings", title: "Настройки",
      text: "Подписка, папка для готовых отчётов, Word или LibreOffice для доводки. Здесь же — «Пройти обучение заново»." }
  ].filter(function (s) { return !s.at || document.querySelector('[data-tour="' + s.at + '"]'); });
  var MOVE = "400ms cubic-bezier(0.1, 0.9, 0.2, 1)";
  var i = 0, closed = false;

  var root = document.createElement("div");
  root.className = "tour";
  root.innerHTML =
    '<div class="tour-catch"></div><div class="tour-hole"></div>' +
    '<div class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-t" aria-describedby="tour-x">' +
    '<div class="tour-beak"></div>' +
    '<div class="tour-head"><h2 id="tour-t"></h2><button type="button" class="tour-close" aria-label="Закрыть обучение">' +
    '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M1 1l10 10M11 1L1 11" stroke="currentColor" stroke-width="1.4"/></svg>' +
    "</button></div><p id=\"tour-x\"></p>" +
    '<div class="tour-foot"><span class="tour-count"></span>' +
    '<span class="spacer"></span><button type="button" class="btn tour-back"></button>' +
    '<button type="button" class="btn primary tour-next"></button></div></div>';
  document.body.appendChild(root);
  var hole = root.querySelector(".tour-hole"), card = root.querySelector(".tour-card"),
      beak = root.querySelector(".tour-beak"), back = root.querySelector(".tour-back"),
      next = root.querySelector(".tour-next");

  function target(step) {
    if (!step.at) return null;
    var el = document.querySelector('[data-tour="' + step.at + '"]');
    if (!el) return null;
    var r = el.getBoundingClientRect();
    return r.width && r.height ? r : null;
  }

  function place(animate) {
    var step = STEPS[i], r = target(step), vw = window.innerWidth, vh = window.innerHeight, pad = 6;
    hole.style.transition = animate ? "top " + MOVE + ", left " + MOVE + ", width " + MOVE + ", height " + MOVE : "none";
    if (r) {
      hole.style.top = r.top - pad + "px"; hole.style.left = r.left - pad + "px";
      hole.style.width = r.width + pad * 2 + "px"; hole.style.height = r.height + pad * 2 + "px";
      hole.classList.add("on");
    } else {
      hole.style.top = vh / 2 + "px"; hole.style.left = vw / 2 + "px";
      hole.style.width = "0px"; hole.style.height = "0px";
      hole.classList.remove("on");
    }
    var cw = card.offsetWidth, ch = card.offsetHeight, m = 12, gap = 14, top, left, below = true;
    if (r) {
      below = r.bottom + gap + ch + m <= vh || r.top - gap - ch < m;
      top = below ? r.bottom + pad + gap : r.top - pad - gap - ch;
      left = Math.min(Math.max(r.left + r.width / 2 - cw / 2, m), vw - cw - m);
      var bx = Math.min(Math.max(r.left + r.width / 2 - left, 18), cw - 18);
      beak.style.left = bx - 7 + "px";
      beak.className = "tour-beak " + (below ? "up" : "down");
      card.style.transformOrigin = bx + "px " + (below ? "0" : "100%");
    } else {
      top = (vh - ch) / 2; left = (vw - cw) / 2;
      beak.className = "tour-beak hidden";
      card.style.transformOrigin = "50% 50%";
    }
    card.style.top = Math.max(m, top) + "px"; card.style.left = left + "px";
  }

  function show(n, animate) {
    i = n;
    var step = STEPS[i], last = i === STEPS.length - 1;
    root.querySelector("#tour-t").textContent = step.title;
    root.querySelector("#tour-x").textContent = step.text;
    root.querySelector(".tour-count").textContent = i === 0 ? "" : i + " из " + (STEPS.length - 1);
    back.textContent = i === 0 ? "Пропустить" : "Назад";
    next.textContent = i === 0 ? "Начать" : last ? "Готово" : "Далее";
    card.classList.remove("in");
    void card.offsetWidth; // перезапуск анимации появления
    place(animate);
    card.classList.add("in");
    next.focus({ preventScroll: true, focusVisible: false });
  }

  function finish() {
    if (closed) return;
    closed = true;
    root.classList.add("out");
    try { fetch("/tour/done", { method: "POST", credentials: "same-origin" }); } catch (e) { /* без сети — покажется ещё раз */ }
    setTimeout(function () { root.remove(); }, 160);
  }

  next.addEventListener("click", function () { if (i === STEPS.length - 1) finish(); else show(i + 1, true); });
  back.addEventListener("click", function () { if (i === 0) finish(); else show(i - 1, true); });
  root.querySelector(".tour-close").addEventListener("click", finish);
  document.addEventListener("keydown", function (e) {
    if (closed) return;
    if (e.key === "Escape") { e.preventDefault(); finish(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); next.click(); }
    else if (e.key === "ArrowLeft" && i > 0) { e.preventDefault(); show(i - 1, true); }
    else if (e.key === "Tab") { // фокус не уходит из карточки
      var f = Array.prototype.slice.call(card.querySelectorAll("button"));
      var k = f.indexOf(document.activeElement);
      e.preventDefault();
      f[(k + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
    }
  });
  window.addEventListener("resize", function () { if (!closed) place(false); });
  requestAnimationFrame(function () { root.classList.add("ready"); show(0, false); });
})();
