"""打包智能体部分为 zip，放到 D:\\涂欣雨个人\\比赛。

用法:
    python scripts/package_for_contest.py            # 脱敏版（无密钥，可提交/外发）
    python scripts/package_for_contest.py --with-key # 含密钥版（仅本地自用，严禁外发）

规则:
- 包含: app/ data/ docs/ scripts/ README.md requirements.txt start.bat
- 排除: venv/ tools/ start_public.bat __pycache__ 及 data 下临时文件
- 脱敏版: start.bat 密钥置空并注释说明；含密钥版: start.bat 原样保留
"""
import argparse
import re
import zipfile
from pathlib import Path

SRC = Path(r"D:\涂欣雨个人\海之子杯-法律智能体")
DST_DIR = Path(r"D:\涂欣雨个人\比赛")
DST_DIR.mkdir(parents=True, exist_ok=True)

EXCLUDE_DIRS = {"venv", "tools", "__pycache__", ".git", ".idea"}
EXCLUDE_FILES = {"start_public.bat", "start.bat", "package_for_contest.py"}


def collect(base: Path, rel: Path) -> list[Path]:
    files = []
    for p in sorted(rel.iterdir()):
        if p.name in EXCLUDE_DIRS:
            continue
        if p.is_dir():
            files.extend(collect(base, p))
        else:
            if p.name in EXCLUDE_FILES:
                continue
            if p.parent.name == "data" and p.name.startswith("_"):
                continue
            files.append(p)
    return files


def start_bat_for(keep_key: bool) -> str:
    raw = (SRC / "start.bat").read_text(encoding="utf-8")
    if keep_key:
        return raw  # 含密钥完整版：用户本地 start.bat 原样（仅自用）
    clean = re.sub(r"set LLM_API_KEY=sk-\w+", "set LLM_API_KEY=", raw)
    clean = clean.replace(
        "REM 配置大模型（OpenAI 兼容协议，DeepSeek 默认）",
        "REM 配置大模型（OpenAI 兼容协议，DeepSeek 默认）\n"
        "REM 【重要】请将下面一行填入你自己的 DeepSeek API Key：\n"
        "REM 例如：set LLM_API_KEY=sk-xxxxxxxx\n"
        "REM 未填写 Key 时智能体以“条文直出”模式工作（可正常演示检索与引用）",
    )
    return clean


def main():
    parser = argparse.ArgumentParser(description="打包智能体到比赛目录")
    parser.add_argument("--with-key", action="store_true", help="生成含密钥版（仅本地自用，严禁外发）")
    args = parser.parse_args()

    zip_name = "海之子杯-法律智能体-含密钥本地版.zip" if args.with_key else "海之子杯-法律智能体.zip"
    zip_path = DST_DIR / zip_name
    if zip_path.exists():
        zip_path.unlink()

    files = collect(SRC, SRC)
    rel_list = [p.relative_to(SRC) for p in files]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(SRC))
        zf.writestr("start.bat", start_bat_for(args.with_key))

    print("打包完成:", zip_path)
    print("模式:", "含密钥版（仅自用）" if args.with_key else "脱敏版（可外发）")
    print("文件数:", len(rel_list) + 1)
    print("大小(MB):", round(zip_path.stat().st_size / 1024 / 1024, 2))


if __name__ == "__main__":
    main()
