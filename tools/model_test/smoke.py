"""Быстрая проверка провайдера модели: отвечает ли, как устроено рассуждение, видит ли картинки, скорость,
и чей у модели токенизатор (по разнице prompt_tokens двух запросов против токенизаторов Qwen, DeepSeek, GLM).

    NG_KEY=... python tools/model_test/smoke.py OUT_DIR

Ключ берётся из окружения и никуда не пишется."""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

BASE = os.environ.get("NG_BASE", "https://2.27.20.129/w/v1").rstrip("/")
MODEL = os.environ.get("NG_MODEL", "qwen3.8-max-0902")
KEY = os.environ["NG_KEY"]
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
H = {"Authorization": f"Bearer {KEY}"}
client = httpx.Client(timeout=300)
REPORT: dict = {"base": BASE, "model": MODEL, "probes": {}}


def scrub(x):
    s = json.dumps(x, ensure_ascii=False)
    return json.loads(s.replace(KEY, "***"))


def chat(name: str, messages, **extra) -> dict:
    body = {"model": MODEL, "messages": messages, **extra}
    t = time.time()
    rec: dict = {"params": {k: v for k, v in extra.items()}}
    try:
        r = client.post(f"{BASE}/chat/completions", headers=H, json=body)
        rec["http"] = r.status_code
        rec["seconds"] = round(time.time() - t, 2)
        try:
            d = r.json()
        except Exception:  # noqa: BLE001
            rec["body"] = r.text[:2000]
            REPORT["probes"][name] = scrub(rec)
            return rec
        if r.status_code != 200:
            rec["body"] = d
        else:
            ch = (d.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            rec.update(model=d.get("model"), finish=ch.get("finish_reason"), usage=d.get("usage"),
                       content=(msg.get("content") or "")[:3000],
                       reasoning_len=len(msg.get("reasoning_content") or msg.get("reasoning") or ""),
                       reasoning_head=(msg.get("reasoning_content") or msg.get("reasoning") or "")[:600],
                       extra_keys=sorted(set(d) - {"choices", "usage", "model", "id", "object", "created"}),
                       msg_keys=sorted(msg))
            u = d.get("usage") or {}
            ct = u.get("completion_tokens") or 0
            rec["tok_per_s"] = round(ct / max(rec["seconds"], 0.01), 1)
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"[:500]
        rec["seconds"] = round(time.time() - t, 2)
    REPORT["probes"][name] = scrub(rec)
    print(name, json.dumps(scrub({k: rec.get(k) for k in ("http", "seconds", "model", "finish", "error")}),
                           ensure_ascii=False), flush=True)
    return rec


def stream_speed() -> None:
    body = {"model": MODEL, "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 1500,
            "messages": [{"role": "user", "content": "Напиши на русском связный текст примерно на 500 слов о том, "
                                                     "как устроена сеть Фейстеля в шифре DES. Без списков."}]}
    t = time.time()
    first = None
    chunks = 0
    usage = None
    rec: dict = {}
    try:
        with client.stream("POST", f"{BASE}/chat/completions", headers=H, json=body) as r:
            rec["http"] = r.status_code
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    d = json.loads(data)
                except Exception:  # noqa: BLE001
                    continue
                if d.get("usage"):
                    usage = d["usage"]
                for c in d.get("choices") or []:
                    delta = c.get("delta") or {}
                    if delta.get("content") or delta.get("reasoning_content"):
                        chunks += 1
                        if first is None:
                            first = time.time() - t
        total = time.time() - t
        ct = (usage or {}).get("completion_tokens") or 0
        rec.update(ttft=round(first or 0, 2), total=round(total, 2), chunks=chunks, usage=usage,
                   gen_tok_per_s=round(ct / max(total - (first or 0), 0.01), 1) if ct else None)
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"[:500]
    REPORT["probes"]["stream_speed"] = scrub(rec)
    print("stream_speed", json.dumps(scrub(rec), ensure_ascii=False)[:400], flush=True)


def test_image() -> str:
    """Картинка «как окно редактора»: панель со списком объектов и красный круг в известном месте."""
    im = Image.new("RGB", (900, 560), (56, 56, 56))
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except OSError:
        f = ImageFont.load_default()
    d.rectangle([0, 0, 220, 560], fill=(40, 40, 40))
    d.text((10, 10), "Hierarchy", fill=(230, 230, 230), font=f)
    for i, name in enumerate(["Main Camera", "Directional Light", "Player", "Ground", "Coin (3)"]):
        d.text((24, 50 + 30 * i), name, fill=(200, 200, 200), font=f)
    d.rectangle([680, 0, 900, 560], fill=(45, 45, 45))
    d.text((690, 10), "Inspector", fill=(230, 230, 230), font=f)
    d.text((690, 50), "Speed: 7.5", fill=(200, 200, 200), font=f)
    d.ellipse([560, 400, 620, 460], fill=(220, 30, 30))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    (OUT / "vision_test.png").write_bytes(buf.getvalue())
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


TEXT = ("Лабораторная работа №5. Реализация функции шифрования алгоритма для криптосистемы DES. "
        "public static string Encrypt(string text, string key) { var bytes = Encoding.UTF8.GetBytes(text); "
        "for (int i = 0; i < 16; i++) { L = R; R = Xor(L0, F(R0, K[i])); } return Convert.ToHexString(bytes); } "
        "The quick brown fox jumps over the lazy dog 1234567890. Таблица 3 – Результат шифрования по блокам. "
        "SELECT name, COUNT(*) FROM students GROUP BY name HAVING COUNT(*) > 1 ORDER BY 2 DESC; "
        "Студентка выполнила задание и сделала вывод о проделанной работе.") * 3


def tokenizers_check() -> None:
    from tokenizers import Tokenizer

    repos = {"qwen3": "Qwen/Qwen3-8B", "deepseek-v3": "deepseek-ai/DeepSeek-V3", "glm-4.5": "zai-org/GLM-4.5"}
    counts = {}
    for name, repo in repos.items():
        try:
            p = OUT / f"tok_{name}.json"
            urllib.request.urlretrieve(f"https://huggingface.co/{repo}/resolve/main/tokenizer.json", p)
            counts[name] = len(Tokenizer.from_file(str(p)).encode(TEXT, add_special_tokens=False).ids)
            p.unlink()
        except Exception as e:  # noqa: BLE001
            counts[name] = f"ошибка: {e}"[:200]
    a = chat("tok_a", [{"role": "user", "content": "x"}], max_tokens=1, enable_thinking=False)
    b = chat("tok_b", [{"role": "user", "content": "x" + "\n" + TEXT}], max_tokens=1, enable_thinking=False)
    pa = ((a.get("usage") or {}).get("prompt_tokens"))
    pb = ((b.get("usage") or {}).get("prompt_tokens"))
    REPORT["tokenizer"] = {"local_counts": counts, "api_prompt_a": pa, "api_prompt_b": pb,
                           "api_delta": (pb - pa) if isinstance(pa, int) and isinstance(pb, int) else None}
    print("tokenizer", REPORT["tokenizer"], flush=True)


def main() -> None:
    try:
        r = client.get(f"{BASE}/models", headers=H)
        REPORT["models"] = {"http": r.status_code, "body": r.text[:3000]}
    except Exception as e:  # noqa: BLE001
        REPORT["models"] = {"error": str(e)[:300]}
    who = [{"role": "user", "content": "Какая ты модель, какой версии и какая компания тебя сделала? Ответь одним "
                                       "предложением."}]
    chat("plain", who, max_tokens=4000)
    task = [{"role": "user", "content": "В классе 30 учеников. 18 изучают английский, 14 – немецкий, 5 не изучают "
                                        "ни одного. Сколько изучают оба языка? Дай только число и одну строку "
                                        "пояснения."}]
    chat("think_on", task, max_tokens=8000, enable_thinking=True)
    chat("think_off", task, max_tokens=2000, enable_thinking=False)
    chat("effort_low", task, max_tokens=8000, reasoning_effort="low")
    chat("effort_high", task, max_tokens=16000, reasoning_effort="high")
    chat("system_json", [{"role": "system", "content": "Отвечай только JSON."},
                         {"role": "user", "content": "Верни объект {\"ok\": true, \"n\": 3}."}],
         max_tokens=500, enable_thinking=False, response_format={"type": "json_object"})
    url = test_image()
    q = ("Это снимок окна редактора. Перечисли все объекты в панели Hierarchy, назови значение Speed в панели "
         "Inspector и скажи, где находится красный круг: координаты его центра в пикселях (картинка 900x560).")
    chat("vision", [{"role": "user", "content": [{"type": "text", "text": q},
                                                 {"type": "image_url", "image_url": {"url": url}}]}],
         max_tokens=3000, enable_thinking=False)
    stream_speed()
    try:
        tokenizers_check()
    except Exception as e:  # noqa: BLE001
        REPORT["tokenizer"] = {"error": str(e)[:300]}
    (OUT / "smoke.json").write_text(json.dumps(scrub(REPORT), ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
