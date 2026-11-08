"""Проверки готового текста и точечная переделка моделью только отмеченных блоков."""

from __future__ import annotations

import json
import re
from collections import defaultdict

from pydantic import BaseModel, Field

from forlabs.antiai.code import scan_code
from forlabs.antiai.similarity import scan_similarity
from forlabs.antiai.text_ru import scan_paragraph_openings, scan_text
from forlabs.core.models import (
    DashList, Finding, LabSpec, Listing, NumberedList, Paragraph, Profile, ReportDoc,
)
from forlabs.core.rules import RuleBook
from forlabs.pipeline.prompts import REPAIR_SYSTEM
from forlabs.report.standard import check_standard

# замечания, которые модель исправить не может (их делает код или пользователь)
STRUCTURAL = {"header.missing", "header.date_format", "task.sequence", "task.title_mismatch", "qa.not_in_spec",
              "conclusion.formula"}
_LOC = re.compile(r"Задание (\d+), блок (\d+)")


def report_text(doc: ReportDoc) -> str:
    parts = []
    for s in doc.sections:
        for b in s.blocks:
            if isinstance(b, Paragraph):
                parts.append(b.text)
            elif isinstance(b, (DashList, NumberedList)):
                parts.extend(b.items)
            elif isinstance(b, Listing):
                parts.append(b.code)
    parts += [q.answer for q in doc.questions]
    return "\n".join(parts)


def check_all(doc: ReportDoc, spec: LabSpec, profile: Profile, others: dict[str, str]) -> list[Finding]:
    out = check_standard(doc, spec, profile.gender)
    paragraphs = []
    for s in doc.sections:
        for bi, b in enumerate(s.blocks, start=1):
            loc = f"Задание {s.n}, блок {bi}"
            if isinstance(b, Paragraph):
                out += scan_text(b.text, loc)
                paragraphs.append((loc, b.text))
            elif isinstance(b, (DashList, NumberedList)):
                for item in b.items:
                    out += scan_text(item, loc)
            elif isinstance(b, Listing):
                out += scan_code(b.code, b.language, loc)
    for q in doc.questions:
        out += scan_text(q.answer, f"Вопрос {q.n}")
    out += scan_paragraph_openings(paragraphs)
    if others:
        out += scan_similarity(report_text(doc), others)
    return out


class RepairBlock(BaseModel):
    index: int
    text: str | None = None
    items: list[str] | None = None
    code: str | None = None


class RepairDraft(BaseModel):
    blocks: list[RepairBlock] = Field(default_factory=list)


def _block_json(b) -> dict:
    if isinstance(b, Paragraph):
        return {"text": b.text}
    if isinstance(b, (DashList, NumberedList)):
        return {"items": b.items}
    if isinstance(b, Listing):
        return {"code": b.code, "language": b.language}
    return {}


def repair(doc: ReportDoc, findings: list[Finding], llm, rules: RuleBook, spec: LabSpec, profile: Profile) -> ReportDoc:
    doc = doc.model_copy(deep=True)
    by_section: dict[int, dict[int, list[Finding]]] = defaultdict(lambda: defaultdict(list))
    by_question: dict[int, list[Finding]] = defaultdict(list)
    for f in findings:
        if f.rule_id in STRUCTURAL:
            continue
        m = _LOC.match(f.location)
        if m:
            by_section[int(m.group(1))][int(m.group(2))].append(f)
        elif f.location.startswith("Вопрос "):
            by_question[int(f.location.split()[1])].append(f)
    system = REPAIR_SYSTEM.format(rules=rules.load("repair", spec.subject_id))
    gender = "женский" if profile.gender == "f" else "мужской"
    for sec in doc.sections:
        flagged = by_section.get(sec.n)
        if not flagged:
            continue
        payload = []
        for bi, fs in sorted(flagged.items()):
            b = sec.blocks[bi - 1]
            payload.append({"index": bi, "type": b.type, **_block_json(b),
                            "замечания": [f"{f.message}" + (f" (как исправить: {f.suggestion})" if f.suggestion else "")
                                          for f in fs]})
        user = (f"Пол студента: {gender}. Задание {sec.n}: «{sec.title}».\nБлоки с замечаниями:\n"
                + json.dumps(payload, ensure_ascii=False, indent=1))
        draft = llm.json([{"role": "system", "content": system}, {"role": "user", "content": user}], RepairDraft)
        for rb in draft.blocks:
            if rb.index not in flagged or not (1 <= rb.index <= len(sec.blocks)):
                continue
            b = sec.blocks[rb.index - 1]
            if isinstance(b, Paragraph) and rb.text:
                b.text = rb.text.strip()
            elif isinstance(b, (DashList, NumberedList)) and rb.items:
                b.items = [x.strip() for x in rb.items if x.strip()]
            elif isinstance(b, Listing) and rb.code:
                b.code = rb.code.rstrip()
    if by_question:
        payload = [{"index": n, "вопрос": doc.questions[n - 1].question, "text": doc.questions[n - 1].answer,
                    "замечания": [f.message for f in fs]} for n, fs in sorted(by_question.items())
                   if 1 <= n <= len(doc.questions)]
        user = f"Пол студента: {gender}. Ответы на контрольные вопросы с замечаниями:\n" + \
               json.dumps(payload, ensure_ascii=False, indent=1)
        draft = llm.json([{"role": "system", "content": system}, {"role": "user", "content": user}], RepairDraft)
        for rb in draft.blocks:
            if rb.index in by_question and rb.text and 1 <= rb.index <= len(doc.questions):
                doc.questions[rb.index - 1].answer = rb.text.strip()
    return doc


def check_and_repair(doc: ReportDoc, spec: LabSpec, profile: Profile, llm, rules: RuleBook,
                     others: dict[str, str], rounds: int = 3, on_event=None) -> tuple[ReportDoc, list[Finding]]:
    emit = on_event or (lambda s: None)
    findings = check_all(doc, spec, profile, others)
    for r in range(rounds):
        fixable = [f for f in findings if f.rule_id not in STRUCTURAL]
        if not fixable:
            break
        emit(f"Проверка: {len(fixable)} замечаний, исправляю (круг {r + 1} из {rounds})")
        doc = repair(doc, fixable, llm, rules, spec, profile)
        findings = check_all(doc, spec, profile, others)
    return doc, findings
