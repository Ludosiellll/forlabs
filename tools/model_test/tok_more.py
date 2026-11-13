"""Счёт токенов одного и того же текста разными токенизаторами (без запросов к провайдеру).
Сравнивается с разницей prompt_tokens у провайдера из быстрой проверки (smoke.json → tokenizer.api_delta)."""
import json
import sys
import urllib.request
from pathlib import Path

out = Path(sys.argv[1])
sys.argv = [sys.argv[0], str(out)]
sys.path.insert(0, str(Path(__file__).parent))
import smoke  # noqa: E402  (только текст TEXT)
from tokenizers import Tokenizer  # noqa: E402

FAMILIES = [("Qwen", "Qwen3.5"), ("Qwen", "Qwen3.6"), ("Qwen", "Qwen3.7"), ("Qwen", "Qwen3.8"), ("Qwen", "Qwen3-"),
            ("zai-org", "GLM-5"), ("zai-org", "GLM-4.6"), ("deepseek-ai", "DeepSeek-V3.2"),
            ("deepseek-ai", "DeepSeek-V4"), ("openai", "gpt-oss"), ("MiniMaxAI", "MiniMax-M2"),
            ("moonshotai", "Kimi-K2")]
res = {}
for author, search in FAMILIES:
    try:
        url = f"https://huggingface.co/api/models?author={author}&search={search}&sort=downloads&limit=8"
        names = [m["id"] for m in json.load(urllib.request.urlopen(url, timeout=30))]
    except Exception as e:  # noqa: BLE001
        res[search] = f"поиск: {e}"[:150]
        continue
    for name in names:
        try:
            p = out / "tok.json"
            urllib.request.urlretrieve(f"https://huggingface.co/{name}/resolve/main/tokenizer.json", p)
            res[name] = len(Tokenizer.from_file(str(p)).encode(smoke.TEXT, add_special_tokens=False).ids)
            p.unlink()
            break
        except Exception as e:  # noqa: BLE001
            res.setdefault(search, []).append(f"{name}: {type(e).__name__}")
(out / "tok_more.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
