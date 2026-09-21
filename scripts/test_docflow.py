"""模拟前端多轮文书流程验证脚本。

调用方式: python scripts/test_docflow.py
模拟用户点击【代写劳动法律文书】→ 逐轮提供信息 → 最后生成文书。
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000/api/chat"


def chat(question):
    req = urllib.request.Request(
        BASE, data=json.dumps({"question": question}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def build_payload(history, question):
    """与前端 buildDocPayload 保持一致：只累积用户消息，超长裁剪。"""
    user_msgs = [h for h in history if h.startswith("用户：")]
    compacted = [h[:250] + "…" if len(h) > 250 else h for h in user_msgs[-14:]]
    payload = "【用户已提供的信息】\n" + "\n".join(compacted) + "\n\n【用户当前输入】\n" + question
    while len(payload) > 1950 and len(compacted) > 1:
        compacted = compacted[1:]
        payload = "【用户已提供的信息】\n" + "\n".join(compacted) + "\n\n【用户当前输入】\n" + question
    if len(payload) > 1950:
        payload = "【用户当前输入】\n" + question[:1500]
    return payload


def main():
    history = []
    turns = [
        "代写劳动法律文书",
        "我叫王建军，男，1990年5月出生，身份证号440111199005123456，住广东省东莞市南城区某某路1号，电话13800001111",
        "被申请人是东莞市某建筑工程有限公司，住东莞市南城区某某大道2号，法定代表人李强，职务总经理",
        "我2024年3月1日入职，做钢筋工，约定每月工资5000元。公司从2026年4月起拖欠工资，到6月共欠3个月共15000元",
        "我在职，没签书面劳动合同。公司一直不给我签。我有工资条和银行转账记录可以证明工资标准",
        "我的仲裁请求是：1、支付拖欠工资15000元；2、支付未签劳动合同的二倍工资差额；3、支付经济补偿金。证据有工资条、银行流水、考勤记录",
        "确认仲裁请求金额：1、支付拖欠2026年4月至6月工资15000元；2、未签劳动合同二倍工资差额，从2024年4月1日至2025年2月28日共11个月，按每月5000元计算，共55000元；3、经济补偿金不再主张。证据：工资条、银行流水、考勤记录，都是我本人的",
    ]

    for i, turn in enumerate(turns, 1):
        q = build_payload(history, turn) if history else turn
        d = chat(q)
        history.append("用户：" + turn)
        history.append("助手：" + d["answer"])
        print(f"\n===== 第 {i} 轮：{turn[:24]}... =====")
        print(d["answer"][:300])
        print("...(截断)")

    # 检查最后一轮是否生成了完整文书
    final = history[-1]
    print("\n===== 流程检查 =====")
    checks = {
        "第1轮输出必备信息清单": "必备信息清单" in history[1] or "第一" in history[1],
        "信息不全时持续追问不生成(无文书结构)": "此致" not in history[3] and "仲裁请求：" not in history[3],
        "信息齐全后生成完整文书": "此致" in final and "申请人" in final,
        "文书含仲裁请求分项": "仲裁请求" in final and "15000" in final and "55000" in final,
        "文书含事实与理由": "事实与理由" in final,
        "文书含证据清单": "证据" in final and "清单" in final,
        "文书含此致仲裁委": "此致" in final and ("仲裁委员会" in final or "仲裁委" in final),
    }
    ok = True
    for name, passed in checks.items():
        print(("  [PASS] " if passed else "  [FAIL] ") + name)
        ok = ok and passed
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
