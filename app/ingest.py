"""命令行入库工具。

用法:
    python -m app.ingest init                     # 初始化数据库
    python -m app.ingest add data/seed/*.json     # 新增文书（批量）
    python -m app.ingest update <doc_id> <new_version.json> --note "修订说明"
    python -m app.ingest repeal <doc_id> --date 2026-01-01 --reason "..."
    python -m app.ingest verify <doc_id>          # 标记人工核验通过
    python -m app.ingest validate                  # 全库时效/状态校验
    python -m app.ingest list                      # 文书清单
"""

import argparse
import json
import sys
from pathlib import Path

from . import config, db, validate, versioning


def _load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        sys.exit(f"文件不存在: {p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def cmd_init(_args):
    conn = db.connect()  # 建表
    conn.close()
    print("数据库已初始化:", config.DB_PATH)


def cmd_add(args):
    added, skipped = 0, []
    with db.get_conn() as conn:
        for path in args.files:
            data = _load_json(path)
            for doc in data.get("documents", [data] if "provisions" in data else []):
                problems = validate.validate_seed_document(doc)
                if problems:
                    skipped.append((doc.get("id", "?"), problems))
                    continue
                if db.doc_exists(conn, doc["id"]):
                    skipped.append((doc["id"], ["已存在，如需更新请用 update 命令"]))
                    continue
                version_id = f"ver_{doc['id']}_v1"
                db.insert_document(conn, doc, version_id)
                added += 1
    print(f"新增文书: {added}")
    for sid, probs in skipped:
        print(f"  跳过 {sid}: {probs}")


def cmd_update(args):
    data = _load_json(args.file)
    # 与 add 保持一致：既接受 {"documents":[...]}，也接受裸文书对象
    if "documents" in data:
        matches = [d for d in data["documents"] if d.get("id") == args.doc_id]
        if not matches:
            sys.exit(f"JSON 中没有 id={args.doc_id} 的文书")
        data = matches[0]
    doc_patch = {k: data[k] for k in ("revision_date", "document_number", "status")
                 if k in data}
    with db.get_conn() as conn:
        problems = validate.validate_seed_document(data)
        if problems:
            sys.exit(f"校验失败: {problems}")
        if not db.doc_exists(conn, args.doc_id):
            sys.exit(f"文书不存在: {args.doc_id}")
        if data.get("id") != args.doc_id:
            sys.exit(f"JSON 中 id({data.get('id')}) 与命令行 doc_id({args.doc_id}) 不一致")
        new_ver = versioning.publish_new_version(
            conn, args.doc_id, data.get("provisions", []),
            version_no=data.get("version_no", ""), version_date=data.get("version_date", ""),
            change_note=args.note or data.get("change_note", ""), doc_patch=doc_patch,
        )
    print(f"已发布新版本 {new_ver} -> {args.doc_id}")


def cmd_repeal(args):
    with db.get_conn() as conn:
        versioning.repeal_document(conn, args.doc_id, args.date, args.reason)
    print(f"已废止: {args.doc_id} (废止日期 {args.date})")


def cmd_verify(args):
    with db.get_conn() as conn:
        if not db.doc_exists(conn, args.doc_id):
            sys.exit(f"文书不存在: {args.doc_id}")
        versioning.set_verified(conn, args.doc_id, True)
    print(f"已标记人工核验通过: {args.doc_id}")


def cmd_validate(_args):
    with db.get_conn() as conn:
        report = validate.validate_database(conn)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_list(_args):
    with db.get_conn() as conn:
        rows = validate.summarize_documents(conn)
    if not rows:
        print("（知识库为空）")
        return
    for r in rows:
        tag = {"active": "现行有效", "revised": "已修订", "repealed": "已废止", "draft": "草稿"}[r["status"]]
        ver = "未核验" if not r["verified"] else "已核验"
        print(f"  [{tag}] {r['id']:28s} {r['name']} (生效 {r['enacted_date'] or '-'}) {ver}")


def main():
    parser = argparse.ArgumentParser(description="法律知识库入库工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")
    p_add = sub.add_parser("add")
    p_add.add_argument("files", nargs="+")
    p_update = sub.add_parser("update")
    p_update.add_argument("doc_id")
    p_update.add_argument("file")
    p_update.add_argument("--note", default="")
    p_repeal = sub.add_parser("repeal")
    p_repeal.add_argument("doc_id")
    p_repeal.add_argument("--date", required=True)
    p_repeal.add_argument("--reason", default="")
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("doc_id")
    sub.add_parser("validate")
    sub.add_parser("list")

    args = parser.parse_args()
    {"init": cmd_init, "add": cmd_add, "update": cmd_update,
     "repeal": cmd_repeal, "verify": cmd_verify,
     "validate": cmd_validate, "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    main()
