"""Ревью-3: null от модели в новых полях, регистр kind, вариант «12.5», огромные и 16-битные картинки,
скрипт удаления не задевает соседние папки."""

import io
from pathlib import Path

import pytest
from PIL import Image

from forlabs.core.models import Enrollment, Profile
from forlabs.pipeline.chat import EditPlan, profile_changes
from forlabs.spec.extract import RawSpec


def test_rawspec_accepts_null_kind_and_reason():
    raw = RawSpec.model_validate({"kind": None, "not_lab_reason": None, "topic": "т"})
    assert raw.kind == "lab" and raw.not_lab_reason == ""


def test_editplan_null_profile_and_kind_case():
    plan = EditPlan.model_validate({"kind": " Edit ", "reply": "r", "profile": None,
                                    "tasks": [{"n": 1, "instruction": "x"}]})
    assert plan.kind == "edit" and plan.profile == []
    assert EditPlan.model_validate({"kind": "непонятно", "tasks": [{"n": 2, "instruction": "y"}]}).kind == "edit"
    assert EditPlan.model_validate({"kind": "непонятно"}).kind == "question"


def test_variant_must_be_one_whole_number():
    p = Profile(id="p", full_name="Иванов Иван", title_name="И.И. Иванов")
    e = Enrollment(subject_id="psip", title_subject="ПСИП", variant=3)
    assert profile_changes([{"field": "variant", "value": "12.5"}], p, e) == []
    assert [c.value for c in profile_changes([{"field": "variant", "value": "вариант №12"}], p, e)] == ["12"]


def test_huge_image_is_refused_not_500():
    from forlabs.cloud.support import to_jpeg

    buf = io.BytesIO()
    Image.new("1", (15000, 13000)).save(buf, format="PNG")
    with pytest.raises(ValueError, match="большая"):
        to_jpeg(buf.getvalue())


def test_16bit_grayscale_is_not_white():
    from forlabs.cloud.support import to_jpeg

    buf = io.BytesIO()
    Image.new("I;16", (40, 40), 1000).save(buf, format="PNG")
    out = Image.open(io.BytesIO(to_jpeg(buf.getvalue()))).convert("L")
    assert out.getpixel((10, 10)) < 200


def test_delete_script_does_not_touch_neighbour_folders():
    import base64

    from forlabs.installer.core import delayed_delete_command

    cmd = delayed_delete_command(Path("C:/Users/Ива’н/ForLabs"), 5)
    script = base64.b64decode(cmd[-1]).decode("utf-16-le")
    assert "StartsWith($p + '\\', " in script  # ForLabs\ — не ForLabs2
    assert "Ива’’н" in script  # «умный» апостроф PowerShell тоже считает кавычкой — удвоен
