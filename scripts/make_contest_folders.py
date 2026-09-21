"""在 D:\\涂欣雨个人\\比赛 下创建两个可直接运行的文件夹。

- 海之子杯-法律智能体               : start.bat 无密钥（条文直出/填 key 后完整）
- 海之子杯-法律智能体-含密钥本地版    : start.bat 含真实密钥（完整版，仅自用）

每个文件夹包含完整代码 + venv（可双击 start.bat 直接运行）。
"""
import re
import shutil
from pathlib import Path

SRC = Path(r"D:\涂欣雨个人\海之子杯-法律智能体")
BASE = Path(r"D:\涂欣雨个人\比赛")
TARGETS = {
    "海之子杯-法律智能体": False,
    "海之子杯-法律智能体-含密钥本地版": True,
}
IGNORE = shutil.ignore_patterns("__pycache__", ".git", "*.pyc")


def build_folder(name: str, with_key: bool) -> Path:
    dst = BASE / name
    dst.mkdir(parents=True, exist_ok=True)  # 已存在则覆盖合并，不删除

    # 复制代码/数据/文档
    for item in SRC.iterdir():
        if item.name == "venv":
            shutil.copytree(item, dst / "venv", dirs_exist_ok=True, ignore=IGNORE)
        elif item.is_dir():
            shutil.copytree(item, dst / item.name, dirs_exist_ok=True, ignore=IGNORE)
        else:
            shutil.copy2(item, dst / item.name)

    # start.bat 版本化
    raw = (SRC / "start.bat").read_text(encoding="utf-8")
    if with_key:
        bat = raw  # 含密钥完整版
    else:
        bat = re.sub(r"set LLM_API_KEY=sk-\w+", "set LLM_API_KEY=", raw)
        bat = bat.replace(
            "REM 配置大模型（OpenAI 兼容协议，DeepSeek 默认）",
            "REM 【重要】请填入你自己的 DeepSeek API Key：\n"
            "REM 例如：set LLM_API_KEY=sk-xxxxxxxx\n"
            "REM 未填写 Key 时智能体以“条文直出”模式工作（可正常演示检索与引用）\n"
            "REM 配置大模型（OpenAI 兼容协议，DeepSeek 默认）",
        )
    (dst / "start.bat").write_text(bat, encoding="utf-8")
    return dst


def main():
    for name, with_key in TARGETS.items():
        dst = build_folder(name, with_key)
        size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file()) / 1024 / 1024
        print(f"已创建: {dst}  ({size:.1f} MB, {'含密钥' if with_key else '无密钥'})")


if __name__ == "__main__":
    main()
