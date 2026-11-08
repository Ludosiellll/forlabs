from forlabs.core.models import Enrollment, LabSpec, LabState, Profile, ReportDoc, Task
from forlabs.core.store import Store
from forlabs.core.subjects import SUBJECTS, all_subjects, add_custom_subject, Subject


def make_profile() -> Profile:
    return Profile(
        id="ivanov",
        full_name="Иванов Иван Петрович",
        title_name="И.П. Иванов",
        gender="m",
        group="РС02-23",
        doc_author="Иванов Иван",
        output_root="/tmp/labs",
        enrollments=[
            Enrollment(subject_id="ks", title_subject="Компьютерные сети", teacher="А.А. Петров",
                       academic_year="2025/2026", variant=2, header_style="short"),
            Enrollment(subject_id="ssp", title_subject="Современные системы программирования",
                       teacher="Б.Б. Сидорова", academic_year="2026/2027", variant=14, header_style="long"),
        ],
    )


def test_profile_roundtrip_and_enrollment(tmp_home):
    store = Store()
    p = make_profile()
    store.save_profile(p)
    assert [x.id for x in store.list_profiles()] == ["ivanov"]
    loaded = store.get_profile("ivanov")
    assert loaded == p
    assert loaded.enrollment("ks").variant == 2
    assert loaded.enrollment("nope") is None


def test_builtin_subjects():
    assert SUBJECTS["ks"].default_variant == 2
    assert SUBJECTS["kpiyap"].default_variant == 30
    assert SUBJECTS["psip"].default_header_style == "short"
    assert SUBJECTS["1c"].kind == "1c"


def test_custom_subject_persisted(tmp_home):
    add_custom_subject(Subject(id="oop", name="Объектно-ориентированное программирование",
                               short_name="ООП", kind="code", default_header_style="long", default_variant=14))
    assert "oop" in all_subjects()
    assert "ks" in all_subjects()


def test_lab_state_persistence(tmp_home):
    store = Store()
    spec = LabSpec(subject_id="ssp", number="5.2", date="01.10.2026", topic="Тема", goal="Изучить X",
                   assignment="Выполните задания", tasks=[Task(n=1, text="Сделать X", kind="main")],
                   questions=[], source_text="...")
    st = LabState(profile_id="ivanov", subject_id="ssp", number="5.2", status="parsed", spec=spec)
    store.save_lab(st)
    assert store.lab_dir("ivanov", "ssp", "5.2").name == "ЛР5.2"
    loaded = store.load_lab("ivanov", "ssp", "5.2")
    assert loaded.spec == spec and loaded.status == "parsed"
    assert [s.number for s in store.list_labs("ivanov", "ssp")] == ["5.2"]
    assert store.load_lab("ivanov", "ssp", "9") is None


def test_report_doc_block_union_roundtrip():
    doc = ReportDoc.model_validate({
        "lab_number": "1", "header": {"Тема работы": "X"},
        "sections": [{"n": 1, "title": "Сделать X", "blocks": [
            {"type": "paragraph", "text": "Абзац"},
            {"type": "dash_list", "items": ["первый;", "второй."]},
            {"type": "figure", "topic": "Окно программы", "slot": "Открой окно"},
            {"type": "listing", "topic": "Исходный код", "code": "x = 1", "language": "python"},
            {"type": "table", "topic": "параметры", "rows": [["a", "b"], ["1", "2"]]},
        ]}],
        "questions": [{"n": 1, "question": "Что?", "answer": "Это."}],
        "conclusion": "Изучил X.",
    })
    again = ReportDoc.model_validate_json(doc.model_dump_json())
    assert again == doc
    assert again.sections[0].blocks[2].type == "figure"
