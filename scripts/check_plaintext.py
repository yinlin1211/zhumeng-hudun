"""纯文本格式验证脚本。"""
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    d = json.load(f)

print("degraded:", d["degraded"])
print("========= 回答原文 =========")
print(d["answer"])
print("========= 符号检查 =========")
bad = [c for c in "#`*_>|" if c in d["answer"]]
lines = d["answer"].split("\n")
md_lines = [
    l for l in lines
    if re.match(r"^\s*(#{1,6}\s|\d+[.、)]\s+|[-*+]\s+|---+|```)", l)
]
print("非法符号:", bad if bad else "无")
print("markdown 风格行:", md_lines if md_lines else "无")
print("引用条数:", len(d["citations"]))
print("结论:", "PASS 纯文本" if not bad and not md_lines else "FAIL 含格式符号")
