"""LLM 客户端抽象（OpenAI 兼容协议）。

- 支持通过环境变量配置: LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
  （例如 DeepSeek、通义、智谱、Kimi 等 OpenAI 兼容端点均可直接接入）
- 未配置 API Key 时进入降级模式: generate() 返回 None，
  智能体将退化为“检索结果直出 + 格式说明”，保证系统无 Key 也可完整演示。
"""

import json
import urllib.error
import urllib.request

from . import config


class LLMUnavailable(RuntimeError):
    """LLM 未配置或调用失败。"""


class ChatClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None):
        self.api_key = (api_key if api_key is not None else config.LLM_API_KEY).strip()
        self.base_url = (base_url if base_url is not None else config.LLM_BASE_URL).strip().rstrip("/")
        self.model = model or config.LLM_MODEL
        self.available = bool(self.api_key)

    def generate(self, messages: list[dict], temperature: float = 0.2,
                 max_tokens: int = 2000) -> str:
        """调用 chat/completions，返回助手文本。未配置 Key 抛 LLMUnavailable。"""
        if not self.available:
            raise LLMUnavailable("未配置 LLM_API_KEY，处于降级模式")
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")[:300]
            raise LLMUnavailable(f"LLM HTTP {e.code}: {detail}") from e
        except Exception as e:  # 网络等错误
            raise LLMUnavailable(f"LLM 调用失败: {e}") from e

        try:
            content = (body["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMUnavailable(f"LLM 返回格式异常: {str(body)[:200]}") from e
        # 上游偶发返回空内容（慢链路截断、内容过滤）：按失败处理，走条文直出兜底，
        # 而不是把空白答案丢给用户
        if not content:
            raise LLMUnavailable("LLM 返回空内容")
        return content
