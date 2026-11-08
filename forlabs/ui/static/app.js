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
