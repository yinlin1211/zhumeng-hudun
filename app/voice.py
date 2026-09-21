"""讯飞方言大模型语音听写客户端。

协议：讯飞方言大模型 WebApi
  wss://iat.cn-huabei-1.xf-yun.com/v1

特点：
  - 鉴权用 HMAC-SHA256（不是通用 v2/iat 的 SHA1）
  - 请求结构用 header/parameter/payload（不是 common/business/data）
  - accent=mulacc：202 种方言自动识别，无需指定语种
  - 响应 result.text 是 base64 编码的 JSON，需解码再解析 ws.cw.w
  - 支持流式识别（dwa=wpgs），pgs=rpl 替换/apd 追加

音频要求：PCM 16k 16bit 单声道，最长 60s，每帧 1280 字节，间隔 40ms。
"""

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from . import config

_HOST = "iat.cn-huabei-1.xf-yun.com"
_PATH = "/v1"


class ASRError(RuntimeError):
    """语音识别失败。"""


class ASRNotConfigured(ASRError):
    """讯飞密钥未配置。"""


def _rfc1123_date() -> str:
    """讯飞要求 RFC1123 格式 UTC 时间。"""
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def _build_auth_url(api_key: str, api_secret: str) -> str:
    """构造带鉴权参数的 WebSocket URL（HMAC-SHA256）。"""
    date = _rfc1123_date()
    signature_origin = f"host: {_HOST}\ndate: {date}\nGET {_PATH} HTTP/1.1"
    signature_sha = hmac.new(
        api_secret.encode("utf-8"), signature_origin.encode("utf-8"), hashlib.sha256
    ).digest()
    signature = base64.b64encode(signature_sha).decode()
    authorization_origin = (
        f'api_key="{api_key}",algorithm="hmac-sha256",'
        f'headers="host date request-line",signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode()
    return (
        f"wss://{_HOST}{_PATH}"
        f"?authorization={quote(authorization)}"
        f"&date={quote(date)}&host={_HOST}"
    )


def _extract_pcm(wav_bytes: bytes) -> bytes:
    """从 WAV 字节中提取裸 PCM（跳过头部，找 data 段）。"""
    idx = wav_bytes.find(b"data")
    if idx < 0:
        return wav_bytes[44:]
    return wav_bytes[idx + 8:]


def xfyun_dialect_iat(pcm: bytes, app_id: str, api_key: str,
                      api_secret: str) -> str:
    """调讯飞方言大模型语音听写，返回识别文本。

    pcm: 16k 16bit 单声道 PCM raw
    方言大模型 accent=mulacc 自动识别 202 种方言，无需指定。
    """
    import websocket  # 延迟导入

    url = _build_auth_url(api_key, api_secret)
    ws = websocket.create_connection(url, timeout=30)
    try:
        # 首帧：header(含status=0) + parameter + payload(status=0)
        first = {
            "header": {"app_id": app_id, "status": 0},
            "parameter": {
                "iat": {
                    "language": "zh_cn",
                    "accent": "mulacc",   # 202 种方言自动识别
                    "domain": "slm",
                    "eos": 1800,
                    "ptt": 1,             # 标点
                    "nunum": 1,           # 数字规整
                    "result": {
                        "encoding": "utf8", "compress": "raw", "format": "json",
                    },
                }
            },
            "payload": {
                "audio": {
                    "encoding": "raw", "sample_rate": 16000,
                    "channels": 1, "bit_depth": 16,
                    "status": 0, "seq": 0, "audio": "",
                }
            },
        }
        ws.send(json.dumps(first))

        # 分帧发送音频（每帧 1280 字节，header 也带 status）
        frame_size = 1280
        total = len(pcm)
        seq = 1
        for offset in range(0, total, frame_size):
            chunk = pcm[offset:offset + frame_size]
            is_last = offset + frame_size >= total
            st = 2 if is_last else 1
            frame = {
                "header": {"app_id": app_id, "status": st},
                "payload": {
                    "audio": {
                        "encoding": "raw", "sample_rate": 16000,
                        "channels": 1, "bit_depth": 16,
                        "status": st,
                        "seq": seq,
                        "audio": base64.b64encode(chunk).decode(),
                    }
                }
            }
            seq += 1
            ws.send(json.dumps(frame))
            # 帧间 40ms：用 recv 限速即可（每发一帧收一帧响应）

        # 接收所有响应，按 sn 去重拼接
        # 非流式模式：每个子句一个响应，sn 递增，直接存
        # 即使讯飞仍发流式 rpl：同 sn 后者覆盖前者（后者更完整），不丢字
        sentences: dict[int, str] = {}   # sn -> 该句文本
        while True:
            try:
                raw = ws.recv()
            except Exception as e:
                raise ASRError(f"接收讯飞响应失败: {e}") from e
            if not raw:
                break
            resp = json.loads(raw)
            header = resp.get("header") or {}
            code = header.get("code", -1)
            if code != 0:
                raise ASRError(
                    f"讯飞返回错误 code={code} msg={header.get('message')}"
                )
            payload = resp.get("payload") or {}
            result = payload.get("result") or {}
            text_b64 = result.get("text")
            if text_b64:
                try:
                    decoded = base64.b64decode(text_b64).decode("utf-8")
                    obj = json.loads(decoded)
                except Exception:
                    obj = {}
                sn = obj.get("sn", 0)
                # 拼接本帧 ws.cw.w
                frame_text = "".join(
                    cw.get("w", "")
                    for w in obj.get("ws", [])
                    for cw in w.get("cw", [])
                )
                if frame_text:
                    # 同 sn 后者覆盖前者（流式 rpl：后者更完整；非流式：每个 sn 一句）
                    # 不同 sn 是不同子句，各自保留
                    sentences[sn] = frame_text
            # header.status=2 或 result.status=2 表示会话结束
            if header.get("status") == 2 or result.get("status") == 2:
                break
        # 按 sn 排序拼接所有子句
        return "".join(sentences[k] for k in sorted(sentences.keys()))
    finally:
        try:
            ws.close()
        except Exception:
            pass


def asr(wav_bytes: bytes, dialect: str = "普通话") -> dict:
    """对外入口：WAV -> 文本。

    方言大模型 accent=mulacc 自动识别 202 种方言，
    前端 dialect 参数仅用于 UI 展示，不影响识别。

    返回 {text, dialect, accent, configured, error}
    """
    app_id = config.XFYUN_APPID.strip()
    api_key = config.XFYUN_API_KEY.strip()
    api_secret = config.XFYUN_API_SECRET.strip()
    configured = bool(app_id and api_key and api_secret)

    if not configured:
        return {
            "text": "", "dialect": dialect, "accent": "mulacc",
            "configured": False,
            "error": "讯飞密钥未配置（XFYUN_APPID/API_KEY/API_SECRET），请填写后再用语音功能",
        }

    pcm = _extract_pcm(wav_bytes)
    if len(pcm) < 1600:  # 至少 50ms
        return {
            "text": "", "dialect": dialect, "accent": "mulacc",
            "configured": True, "error": "录音太短，请按住麦克风多说几句",
        }
    try:
        text = xfyun_dialect_iat(pcm, app_id, api_key, api_secret)
    except ASRError as e:
        return {
            "text": "", "dialect": dialect, "accent": "mulacc",
            "configured": True, "error": str(e),
        }
    return {
        "text": text, "dialect": dialect, "accent": "mulacc",
        "configured": True, "error": "",
    }
