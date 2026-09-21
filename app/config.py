"""全局配置。"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_keys_file() -> None:
    """从项目根目录的 keys.env 载入密钥。

    keys.env 不入版本库（见 .gitignore）：交付包内已附带，从 GitHub 获取的副本
    需自行复制 keys.env.example 并填写。环境变量优先于文件，便于服务器用
    systemd 的 EnvironmentFile 覆盖。
    """
    path = BASE_DIR / "keys.env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_keys_file()

# 数据库位置（默认项目根目录下）
DB_PATH = Path(os.environ.get("LEGALKB_DB_PATH", BASE_DIR / "data" / "legalkb.db"))

# 文书效力位阶权重（检索排序用，位阶越高权重越大）
DOC_TYPE_RANK = {
    "constitution": 6,            # 宪法
    "law": 5,                     # 法律
    "judicial_interpretation": 4, # 司法解释
    "administrative_regulation": 3,  # 行政法规
    "rules": 2,                   # 部门规章
    "normative": 1,               # 规范性文件
}

# 效力状态（文书级 / 版本级 / 条款级共用一套状态机）
STATUS_ACTIVE = "active"      # 现行有效
STATUS_REVISED = "revised"    # 已修订（被新版本取代）
STATUS_REPEALED = "repealed"  # 已废止
STATUS_DRAFT = "draft"        # 草稿（未生效）

# 允许的状态迁移: (from, to) -> 说明
STATUS_TRANSITIONS = {
    (STATUS_DRAFT, STATUS_ACTIVE): "草稿发布生效",
    (STATUS_ACTIVE, STATUS_REVISED): "被修订版本取代",
    (STATUS_ACTIVE, STATUS_REPEALED): "被废止",
    (STATUS_REVISED, STATUS_REPEALED): "修订版本最终废止",
    (STATUS_REPEALED, STATUS_ACTIVE): "恢复效力（罕见，需人工核验）",
}

# LLM 环境变量（OpenAI 兼容协议）
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-flash")

# 讯飞开放平台·语音听写（方言版）密钥
# 申请：https://www.xfyun.cn/ → 控制台 → 创建"语音听写"应用
# 免费额度 50000 次/日，参赛演示够用
XFYUN_APPID = os.environ.get("XFYUN_APPID", "")
XFYUN_API_KEY = os.environ.get("XFYUN_API_KEY", "")
XFYUN_API_SECRET = os.environ.get("XFYUN_API_SECRET", "")

# 检索参数
RETRIEVE_TOP_K = int(os.environ.get("LEGALKB_TOP_K", "5"))
# 范围判定阈值（智能体侧）: 覆盖率 >= 0.5 或 连续双字命中 >= 2 视为范围内，见 agent._scope_check

DISCLAIMER = (
    "本回答由 AI 辅助生成，仅供参考，不构成正式法律意见。"
    "如需维权，请咨询执业律师或拨打 12348 法律援助热线 / 12333 人社服务热线。"
)
