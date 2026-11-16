# -*- coding: utf-8 -*-
"""本地 embedding 微服务(OpenAI 兼容 /v1/embeddings), 供 rdagent/litellm hosted_vllm 通道使用。

模型: BAAI/bge-small-zh-v1.5 (HF_ENDPOINT=https://hf-mirror.com 走镜像下载)
运行: 由 wsl_live_run.sh 在 127.0.0.1:9101 拉起(已活则跳过)
"""
import os

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import uvicorn

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
model = SentenceTransformer(MODEL_NAME)

app = FastAPI()


class EmbedRequest(BaseModel):
    model: str = MODEL_NAME
    input: str | list[str]


@app.post("/v1/embeddings")
def embeddings(req: EmbedRequest):
    texts = [req.input] if isinstance(req.input, str) else req.input
    vecs = model.encode(texts, normalize_embeddings=True).tolist()
    return {
        "object": "list",
        "model": MODEL_NAME,
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9101, log_level="warning")
