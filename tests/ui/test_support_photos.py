"""Поддержка: фото от студента и от администратора, показ только своих фото, автообновление переписки."""

import io
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from test_admin import M, admin, fake, make_client  # noqa: E402, F401
from test_cloud import UID  # noqa: E402


def png(w=3000, h=1500, color=(200, 30, 30), mode="RGBA"):
    buf = io.BytesIO()
    Image.new(mode, (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def member(fake, tmp_home):
    return make_client(fake, tmp_home, me="member")


def test_member_sends_photo_with_text(fake, tmp_home):
    c = member(fake, tmp_home)
    r = c.post("/support", data={"text": "Вот ошибка"}, files=[("images", ("err.png", png(), "image/png"))],
               follow_redirects=False)
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    msg = fake.tables["support_messages"][-1]
    assert msg["text"] == "Вот ошибка" and len(msg["images"]) == 1 and msg["images"][0].startswith(UID + "/")
    img = Image.open(io.BytesIO(fake.support_objects[msg["images"][0]]))
    assert img.format == "JPEG" and max(img.size) <= 2560  # уменьшено и в JPEG — Telegram примет как фото
    page = c.get("/support").text
    assert "/support/img?path=" in page
    shown = c.get(f"/support/img?path={msg['images'][0]}")
    assert shown.status_code == 200 and shown.headers["content-type"] == "image/jpeg"


def test_photo_only_message(fake, tmp_home):
    c = member(fake, tmp_home)
    r = c.post("/support", data={"text": ""}, files=[("images", ("a.png", png(400, 300), "image/png"))],
               follow_redirects=False)
    assert "err=" not in r.headers["location"] and fake.tables["support_messages"][-1]["text"] == ""


def test_bad_or_too_many_images_refused(fake, tmp_home):
    from urllib.parse import unquote

    c = member(fake, tmp_home)
    r = c.post("/support", data={"text": "x"}, files=[("images", ("a.txt", b"not an image", "image/png"))],
               follow_redirects=False)
    assert "картинк" in unquote(r.headers["location"])
    files = [("images", (f"{i}.png", png(50, 50), "image/png")) for i in range(5)]
    r = c.post("/support", data={"text": "x"}, files=files, follow_redirects=False)
    assert "4" in unquote(r.headers["location"]) and "err=" in r.headers["location"]


def test_member_cannot_open_foreign_photo(fake, tmp_home):
    c = member(fake, tmp_home)
    fake.support_objects[f"{M}/x.jpg"] = b"secret"
    assert c.get(f"/support/img?path={M}/x.jpg").status_code == 404
    assert c.get("/support/img?path=../etc/passwd").status_code == 404


def test_admin_replies_with_photo_and_sees_student_photo(admin, fake):
    fake.support_objects[f"{M}/s1.jpg"] = png(10, 10, mode="RGB")
    fake.tables["support_messages"] = [{"id": 1, "user_id": M, "from_admin": False, "text": "смотрите",
                                        "images": [f"{M}/s1.jpg"], "created_at": "2026-11-08T10:00:00+00:00",
                                        "read_at": None}]
    page = admin.get(f"/admin/support/{M}").text
    assert f"/support/img?path={M}/s1.jpg" in page
    assert admin.get(f"/support/img?path={M}/s1.jpg").status_code == 200
    r = admin.post(f"/admin/support/{M}", data={"text": "вот так"}, files=[("images", ("a.png", png(100, 80), "image/png"))],
                   follow_redirects=False)
    assert r.status_code == 303
    reply = fake.tables["support_messages"][-1]
    assert reply["from_admin"] is True and reply["images"][0].startswith(M + "/")


def test_log_partial_for_auto_refresh(fake, tmp_home):
    c = member(fake, tmp_home)
    c.post("/support", data={"text": "первое"})
    log = c.get("/support/log")
    assert log.status_code == 200 and 'data-count="1"' in log.text and "первое" in log.text
    assert "<html" not in log.text.lower()
    assert "data-support-refresh" in c.get("/support").text
