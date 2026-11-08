from forlabs.core.models import ChatMessage, LabSpec, LabState, ReportDoc, push_version


def test_old_state_without_chat_loads():
    st = LabState.model_validate({"profile_id": "p", "subject_id": "ssp", "number": "4", "status": "rendered"})
    assert st.chat == [] and st.versions == []


def test_push_version_keeps_last_ten():
    st = LabState(profile_id="p", subject_id="ssp", number="4",
                  spec=LabSpec(subject_id="ssp", number="4", date="01.11.2026", topic="Т", goal="Ц"),
                  report=ReportDoc(lab_number="4"))
    ids = [push_version(st, f"правка {i}") for i in range(12)]
    assert ids == list(range(1, 13))
    assert [v.id for v in st.versions] == list(range(3, 13))
    assert st.versions[-1].label == "правка 11"
    st.report.conclusion = "изменено"
    assert st.versions[-1].report.conclusion == ""  # снимок, а не ссылка


def test_chat_message_defaults():
    m = ChatMessage(id="a", role="user", text="Сделай подробнее", at="2026-11-08T10:00:00")
    assert m.kind == "note" and m.changed == [] and m.version is None
