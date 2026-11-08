from fake_word import LINES, FakeDoc

from forlabs.render.word_com import insert_listing_continuations, listing_bookmarks


def make_doc(code_lines=12, before=3, stale_at=None):
    doc = FakeDoc()
    for i in range(before):
        doc.add(f"Абзац {i + 1}")
    code = []
    for i in range(code_lines):
        if stale_at is not None and i == stale_at:
            doc.add("Продолжение листинга 1")
        code.append(doc.add(f"line {i + 1}", code=True))
    doc.add("Текст после листинга")
    doc.Bookmarks.marks["_ForLabsL8_1"] = (code[0], code[-1])
    return doc


def test_hidden_bookmarks_are_found():
    doc = make_doc()
    assert listing_bookmarks(doc) == ["_ForLabsL8_1"]


def test_continuations_at_page_tops_without_breaking_code():
    doc = make_doc()
    n = insert_listing_continuations(doc)
    assert n >= 2
    conts = [p for p in doc.paras if p.text.startswith("Продолжение")]
    assert len(conts) == n
    for p in conts:
        assert p.text == "Продолжение листинга 1"
        assert p.index % LINES == 0, "продолжение должно стоять в начале страницы"
        assert p.bordered is False and p.font == "Times New Roman"
    code = [p for p in doc.paras if p.code and not p.text.startswith("Продолжение")]
    assert [p.text for p in code] == [f"line {i + 1}" for i in range(12)]
    assert all(p.bordered and p.font == "Consolas" for p in code)


def test_stale_continuation_removed():
    doc = make_doc(code_lines=4, before=0, stale_at=2)  # всё на одной странице — продолжение лишнее
    insert_listing_continuations(doc)
    assert not any(p.text.startswith("Продолжение") for p in doc.paras)


def test_stable_second_run_changes_nothing():
    doc = make_doc()
    insert_listing_continuations(doc)
    before = [p.text for p in doc.paras]
    assert insert_listing_continuations(doc) == 0
    assert [p.text for p in doc.paras] == before
