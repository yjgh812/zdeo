# B7 冒烟: litellm 直连硅基流动 chat+embedding 双通道, 再验 rdagent 包装层。
# 原始位置: D:\zcodesj\.rdagent_ws\smoke_llm.py (B7, 2026-09-07)
# 运行前提: cwd 含 .env (CHAT_OPENAI_API_KEY/BASE_URL + EMBEDDING_* + OPENAI_API_KEY)
import sys

print("=== 1. rdagent import ===")
import rdagent  # noqa: F401
print("rdagent import OK, version:", getattr(rdagent, "__version__", "n/a"))

print("=== 2. litellm direct: chat (SiliconFlow DeepSeek-V3) ===")
import litellm

KEY = "从.env读取, 勿硬编码"
BASE = "https://api.siliconflow.cn/v1"
r = litellm.completion(
    model="openai/deepseek-ai/DeepSeek-V3",
    api_key=KEY, api_base=BASE,
    messages=[{"role": "user", "content": "只回复两个字母: OK"}],
    max_tokens=200, temperature=0,
)
chat_text = r.choices[0].message.content
print("chat resp:", repr(chat_text))

print("=== 3. litellm direct: embedding (BAAI/bge-m3) ===")
e = litellm.embedding(
    model="openai/BAAI/bge-m3",
    api_key=KEY, api_base=BASE,
    input=["屠龙量化冒烟测试"],
)
vec = e.data[0]["embedding"]
print("embedding dim:", len(vec), "head:", [round(x, 4) for x in vec[:3]])

print("=== 4. rdagent wrapper (reads .env in cwd) ===")
from rdagent.oai.llm_conf import LLM_SETTINGS  # noqa: E402
print("loaded chat_model:", LLM_SETTINGS.chat_model)
print("loaded chat base:", LLM_SETTINGS.chat_openai_base_url)
print("loaded embed base:", LLM_SETTINGS.embedding_openai_base_url)

# 后端类实际路径: rdagent.oai.backend.litellm (llm_conf 里的字符串默认值已过时)
from rdagent.oai.backend.litellm import LiteLLMAPIBackend  # noqa: E402
be = LiteLLMAPIBackend()
out = be.build_messages_and_create_chat_completion(
    system_prompt="You are a helpful assistant.",
    user_prompt="只回复: PASS",
)
print("rdagent backend resp:", str(out)[:200])

print("=== SMOKE PASS ===")
