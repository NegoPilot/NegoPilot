"""문장 목록(JSON)을 임베딩해 정규화한 벡터를 .npy 로 저장한다. eval_retrieval.py 가 부른다.

    python3 eval/embed.py bge-m3 texts.json out.npy        # torch, transformers 필요
    python3 eval/embed.py openai-small texts.json out.npy  # openai, OPENAI_API_KEY 필요
"""

import json
import os
import sys
from pathlib import Path

import numpy as np


def bge_m3(texts: list[str], batch: int = 16) -> np.ndarray:
    import torch
    from transformers import AutoModel, AutoTokenizer
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained("BAAI/bge-m3")
    model = AutoModel.from_pretrained("BAAI/bge-m3").to(device).eval()
    out = []
    for i in range(0, len(texts), batch):
        enc = tok(texts[i:i + batch], padding=True, truncation=True, max_length=512,
                  return_tensors="pt").to(device)
        with torch.no_grad():
            cls = model(**enc).last_hidden_state[:, 0]          # bge-m3 dense 는 CLS 벡터
        out.append(torch.nn.functional.normalize(cls, dim=-1).cpu().numpy())
    return np.vstack(out)


def openai_small(texts: list[str], batch: int = 128) -> np.ndarray:
    from openai import OpenAI
    if not os.getenv("OPENAI_API_KEY"):
        env = Path(__file__).resolve().parents[3] / ".env"           # 저장소 루트 .env
        for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
            if line.startswith("OPENAI_API_KEY="):
                os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1].strip().strip("'\"")
    client = OpenAI()
    out = []
    for i in range(0, len(texts), batch):
        res = client.embeddings.create(model="text-embedding-3-small", input=texts[i:i + batch])
        out += [d.embedding for d in res.data]
    m = np.array(out, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


MODELS = {"bge-m3": bge_m3, "openai-small": openai_small}

if __name__ == "__main__":
    model, src, dst = sys.argv[1:4]
    texts = json.loads(Path(src).read_text(encoding="utf-8"))
    np.save(dst, MODELS[model](texts).astype(np.float32))
    print(f"{model}: {len(texts)}개 임베딩 → {dst}", file=sys.stderr)
