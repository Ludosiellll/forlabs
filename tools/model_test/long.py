"""Длинные ответы у провайдера: где обрыв (время, длина, рассуждение, поток)."""
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("NG_BASE", "https://2.27.20.129/w/v1").rstrip("/")
MODEL = os.environ.get("NG_MODEL", "qwen3.8-max-0902")
KEY = os.environ["NG_KEY"]
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
H = {"Authorization": f"Bearer {KEY}"}
TASK = ("Напиши на C# полную консольную программу шифрования текста алгоритмом DES (все таблицы IP, E, P, S1–S8, "
        "PC-1, PC-2, сдвиги), с вводом ключа и текста с клавиатуры, и после кода — подробное пояснение на русском "
        "на 600 слов, как работает каждый метод.")
res = []


def one(name, stream, think, max_tokens):
    body = {"model": MODEL, "max_tokens": max_tokens, "enable_thinking": think,
            "messages": [{"role": "user", "content": TASK}]}
    rec = {"name": name, "stream": stream, "think": think}
    t = time.time()
    try:
        with httpx.Client(timeout=httpx.Timeout(900, connect=30)) as c:
            if not stream:
                r = c.post(f"{BASE}/chat/completions", headers=H, json=body)
                rec["http"] = r.status_code
                d = r.json()
                if r.status_code == 200:
                    rec.update(usage=d.get("usage"), finish=d["choices"][0].get("finish_reason"),
                               content_len=len(d["choices"][0]["message"].get("content") or ""))
                else:
                    rec["body"] = str(d)[:300]
            else:
                body["stream"] = True
                body["stream_options"] = {"include_usage": True}
                n, first, last = 0, None, None
                with c.stream("POST", f"{BASE}/chat/completions", headers=H, json=body) as r:
                    rec["http"] = r.status_code
                    for line in r.iter_lines():
                        if not line.startswith("data:") or line.strip() == "data: [DONE]":
                            continue
                        try:
                            d = json.loads(line[5:])
                        except Exception:  # noqa: BLE001
                            rec.setdefault("bad", line[:200])
                            continue
                        if d.get("usage"):
                            rec["usage"] = d["usage"]
                        if d.get("error") or d.get("type") == "error":
                            rec["error_event"] = str(d)[:300]
                        for ch in d.get("choices") or []:
                            if ch.get("finish_reason"):
                                rec["finish"] = ch["finish_reason"]
                            delta = ch.get("delta") or {}
                            if delta.get("content") or delta.get("reasoning_content"):
                                n += 1
                                first = first or time.time() - t
                                last = time.time() - t
                rec.update(chunks=n, first=round(first or 0, 1), last=round(last or 0, 1))
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"[:300].replace(KEY, "***")
    rec["seconds"] = round(time.time() - t, 1)
    res.append(rec)
    print(json.dumps({k: rec.get(k) for k in ("name", "http", "seconds", "finish", "error")}, ensure_ascii=False),
          flush=True)
    (OUT / "long.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


one("plain_nothink", False, False, 16000)
one("plain_think", False, True, 16000)
one("stream_think", True, True, 16000)
one("stream_nothink", True, False, 16000)
