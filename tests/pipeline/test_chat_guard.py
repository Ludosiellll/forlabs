"""Чат правок: только по этой лабе (посторонние просьбы — вежливый отказ), короткие ответы, предложения поменять
данные профиля (руководитель, группа, ФИО…) с подтверждением; проверка, что загружена методичка лабораторной."""

import pytest
from test_chat_run import ready
from test_run import ANS, FIXTURE, RAW, SEC, SEC2, setup

from forlabs.pipeline.run import PipelineError


def test_offtopic_request_is_politely_refused(tmp_home, tmp_path):
    plan = {"kind": "offtopic", "reply": "Чат правок — только по этой лабе. С другим не помогу."}
    pipe, fake, st = ready(tmp_home, tmp_path, [plan])
    before = st.report.model_dump()
    st = pipe.chat(st, "напиши мне курсовой проект по базам данных")
    assert st.report.model_dump() == before and not st.versions
    last = st.chat[-1]
    assert last.kind == "offtopic" and "только по этой лабе" in last.text


def test_answer_is_short(tmp_home, tmp_path):
    plan = {"kind": "answer", "reply": "Очень длинный ответ. " * 400}
    pipe, fake, st = ready(tmp_home, tmp_path, [plan])
    st = pipe.chat(st, "а почему фон движется медленнее?")
    assert len(st.chat[-1].text) <= 1300
    assert fake.max_tokens[-1] <= 4000  # план не длиннее (ответ на вопрос режет ANSWER_LIMIT)


def test_profile_change_is_proposed_not_applied(tmp_home, tmp_path):
    plan = {"kind": "answer", "reply": "Руководитель — это данные профиля, предлагаю поменять.",
            "profile": [{"field": "teacher", "value": "Петров П.П."}, {"field": "group", "value": "РС02-24"},
                        {"field": "hacker", "value": "x"}, {"field": "group", "value": ""}]}
    pipe, fake, st = ready(tmp_home, tmp_path, [plan])
    teacher_before = pipe.store.get_profile("ivanov").enrollment("psip").teacher
    st = pipe.chat(st, "преподаватель теперь Петров П.П., и группа РС02-24")
    last = st.chat[-1]
    assert [(c.field, c.value) for c in last.profile] == [("teacher", "Петров П.П."), ("group", "РС02-24")]
    assert last.profile[0].old == teacher_before and last.profile[0].label
    assert pipe.store.get_profile("ivanov").enrollment("psip").teacher == teacher_before  # без согласия не меняет


def test_apply_profile_proposal_updates_profile_and_rebuilds(tmp_home, tmp_path):
    plan = {"kind": "answer", "reply": "Предлагаю поменять руководителя.",
            "profile": [{"field": "teacher", "value": "Петров П.П."}]}
    pipe, fake, st = ready(tmp_home, tmp_path, [plan])
    st = pipe.chat(st, "руководитель — Петров П.П.")
    msg_id = st.chat[-1].id
    st = pipe.apply_profile_change(st, msg_id, apply=True)
    assert pipe.store.get_profile("ivanov").enrollment("psip").teacher == "Петров П.П."
    assert st.chat[-1 if st.chat[-1].id == msg_id else -2].profile_state == "applied"
    from forlabs.pipeline.run import render_inputs

    prof = pipe.store.get_profile("ivanov")
    assert st.render_inputs == render_inputs(prof, prof.enrollment("psip"))  # документ пересобран с новым профилем
    with pytest.raises(PipelineError):
        pipe.apply_profile_change(st, msg_id, apply=True)  # второй раз не применяется


def test_decline_profile_proposal(tmp_home, tmp_path):
    plan = {"kind": "answer", "reply": "Поменять группу?", "profile": [{"field": "group", "value": "РС02-24"}]}
    pipe, fake, st = ready(tmp_home, tmp_path, [plan])
    st = pipe.chat(st, "группа РС02-24")
    msg_id = st.chat[-1].id
    st = pipe.apply_profile_change(st, msg_id, apply=False)
    assert pipe.store.get_profile("ivanov").group != "РС02-24"
    assert next(m for m in st.chat if m.id == msg_id).profile_state == "declined"


def test_same_value_is_not_proposed(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, [])
    group = pipe.store.get_profile("ivanov").group
    fake.responses.append({"kind": "answer", "reply": "Группа и так такая.", "profile": [{"field": "group", "value": group}]})
    st = pipe.chat(st, f"группа {group}")
    assert st.chat[-1].profile == []


def test_not_a_lab_document_is_rejected(tmp_home, tmp_path):
    raw = {**RAW, "kind": "not_lab", "not_lab_reason": "Это задание на курсовой проект"}
    pipe, fake = setup(tmp_home, tmp_path, [raw])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number=None)
    st = pipe.parse(st, date="24.09.2026")
    assert st.status == "failed" and "курсовой проект" in st.error and "лабораторн" in st.error


def test_lab_kind_defaults_to_lab(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [{**RAW, "kind": "что-то странное"}, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number=None)
    assert pipe.parse(st, date="24.09.2026").status == "parsed"
