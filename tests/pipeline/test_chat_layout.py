"""Чат и оформление. Случай из практики: студентка трижды просила убрать разрывы страниц в таблице (ей нужна
цельная таблица — перенести в свой файл), чат трижды отвечал «пересоберу так, чтобы таблица не разрывалась», и
ничего не менялось. Оформление делает код, а не модель: либо есть переключатель, либо честное «здесь не меняется»."""

import zipfile

from test_chat_run import ready

PLAIN = {"kind": "answer", "reply": "Соберу документ без разрывов страниц и продолжений таблиц.", "layout": "plain"}
BACK = {"kind": "edit", "reply": "Верну вёрстку по образцу.", "layout": "standard"}


def _plain_seen(pipe, monkeypatch):
    import forlabs.pipeline.run as run

    seen = []
    real = run.finalize

    def spy(*a, **k):
        seen.append(bool(k.get("plain")))
        return real(*a, **k)

    monkeypatch.setattr(run, "finalize", spy)
    return seen


def test_request_to_remove_table_breaks_switches_the_layout(tmp_home, tmp_path, monkeypatch):
    pipe, fake, st = ready(tmp_home, tmp_path, [PLAIN, BACK])
    seen = _plain_seen(pipe, monkeypatch)
    st = pipe.chat(st, "удали разрывы таблицы")
    reply = st.chat[-1]
    assert st.spec.plain_layout is True and seen == [True]  # документ действительно собран иначе
    assert reply.kind == "edit" and any("без разрывов" in c for c in reply.changed)
    with zipfile.ZipFile(st.docx_path) as z:
        assert z.read("word/document.xml").count(b'w:type="page"') == 0
    assert "разрывов страниц" in reply.text and "нет" in reply.text  # итог назван по самому документу
    asked = "\n".join(m["content"] for m in fake.calls[-1] if isinstance(m["content"], str))
    assert "Вёрстка сейчас: по образцу" in asked
    st = pipe.chat(st, "верни как было")
    assert st.spec.plain_layout is False and seen == [True, False]
    asked = "\n".join(m["content"] for m in fake.calls[-1] if isinstance(m["content"], str))
    assert "Вёрстка сейчас: без разрывов" in asked
    st = pipe.revert(st, reply.version)  # и кнопкой «Вернуть как было» — тоже
    assert st.spec.plain_layout is False


def test_same_or_unknown_layout_changes_nothing(tmp_home, tmp_path):
    odd = {"kind": "edit", "reply": "Готово.", "layout": "wide"}
    same = {"kind": "edit", "reply": "Готово.", "layout": "standard"}
    pipe, fake, st = ready(tmp_home, tmp_path, [odd, same])
    for _ in range(2):
        st = pipe.chat(st, "поменяй вёрстку")
        assert st.spec.plain_layout is False and st.chat[-1].kind == "question"


def test_chat_is_told_what_formatting_it_can_change(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, [{"kind": "answer", "reply": "Оформление задано образцом."}])
    pipe.chat(st, "сделай шрифт 16")
    system = fake.calls[-1][0]["content"]
    assert '"layout"' in system and "Продолжение таблицы" in system
    assert "пересоберу так, чтобы" in system  # именно такое обещание запрещено, если переключателя нет
    assert "по образцу" in system and "в Word" in system
