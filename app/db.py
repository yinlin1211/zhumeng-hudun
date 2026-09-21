"""数据模型与存储层。

使用 SQLite + FTS5（全文检索）：
- legal_document     文书主表（效力状态、生效/修订/废止日期、核验标记）
- document_version   版本表（一个文书可含多个历史版本，实现版本管理）
- legal_provision    条款表（条文级内容，绑定所属版本）
- legal_provision_fts  FTS5 全文索引（中文按“字符化”预处理以支持检索）

中文检索说明:
    FTS5 默认 tokenizer 不做中文分词，这里在入库时把条文内容做“字符化”
    （每个汉字/符号以空格分隔），查询时同样字符化并用短语匹配，
    以近似 n-gram 的方式支持中文子串/短语召回，避免引入重型分词依赖。
"""

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import config

# 只保留汉字/字母/数字，剔除标点（避免标点 token 干扰 FTS 短语匹配）
_TOKEN_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9]")


def char_tokenize(text: str) -> str:
    """将文本转换为单字符空格分隔的索引串（用于 FTS5 中文检索）。"""
    return " ".join(_TOKEN_RE.findall(text or ""))


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


DDL = """
CREATE TABLE IF NOT EXISTS legal_document (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    doc_type         TEXT NOT NULL DEFAULT 'law',
    issuing_authority TEXT NOT NULL DEFAULT '',
    document_number  TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'active',
    enacted_date     TEXT NOT NULL DEFAULT '',
    revision_date    TEXT NOT NULL DEFAULT '',
    repeal_date      TEXT NOT NULL DEFAULT '',
    source_url       TEXT NOT NULL DEFAULT '',
    verified         INTEGER NOT NULL DEFAULT 0,
    last_checked     TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_version (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES legal_document(id),
    version_no  TEXT NOT NULL,
    version_date TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    change_note TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS legal_provision (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES legal_document(id),
    version_id  TEXT NOT NULL REFERENCES document_version(id),
    article_no  TEXT NOT NULL,
    article_seq INTEGER NOT NULL DEFAULT 0,
    content     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    UNIQUE (document_id, article_no)
);

CREATE VIRTUAL TABLE IF NOT EXISTS legal_provision_fts USING fts5(
    content, article_no, document_id UNINDEXED, article_seq UNINDEXED
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(DDL)
    return conn


@contextmanager
def get_conn(db_path: Path | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------- 数据访问 ----------------

def insert_document(conn, doc: dict, version_id: str, change_note: str = "") -> None:
    """写入文书 + 初始版本 + 条款，并重建 FTS 索引项。"""
    ts = now()
    conn.execute(
        """INSERT INTO legal_document
           (id, name, doc_type, issuing_authority, document_number, status,
            enacted_date, revision_date, repeal_date, source_url, verified,
            last_checked, created_at, updated_at)
           VALUES (:id,:name,:doc_type,:issuing_authority,:document_number,:status,
                   :enacted_date,:revision_date,:repeal_date,:source_url,:verified,
                   :last_checked,:created_at,:updated_at)""",
        {
            "id": doc["id"], "name": doc["name"], "doc_type": doc.get("doc_type", "law"),
            "issuing_authority": doc.get("issuing_authority", ""),
            "document_number": doc.get("document_number", ""),
            "status": doc.get("status", "active"),
            "enacted_date": doc.get("enacted_date", ""),
            "revision_date": doc.get("revision_date", ""),
            "repeal_date": doc.get("repeal_date", ""),
            "source_url": doc.get("source_url", ""),
            "verified": 1 if doc.get("verified") else 0,
            "last_checked": doc.get("last_checked", ""),
            "created_at": ts, "updated_at": ts,
        },
    )
    conn.execute(
        """INSERT INTO document_version (id, document_id, version_no, version_date,
                                         status, change_note, created_at)
           VALUES (:id,:document_id,:version_no,:version_date,:status,:change_note,:created_at)""",
        {
            "id": version_id, "document_id": doc["id"],
            "version_no": doc.get("version_no", "v1"),
            "version_date": doc.get("version_date", ""),
            "status": "active",
            "change_note": change_note, "created_at": ts,
        },
    )
    for i, p in enumerate(doc.get("provisions", []), start=1):
        conn.execute(
            """INSERT INTO legal_provision (id, document_id, version_id, article_no,
                                            article_seq, content, status)
               VALUES (:id,:document_id,:version_id,:article_no,:article_seq,:content,:status)""",
            {
                "id": f"{doc['id']}::{p['article_no']}",
                "document_id": doc["id"], "version_id": version_id,
                "article_no": p["article_no"], "article_seq": p.get("article_seq", i),
                "content": p["content"], "status": p.get("status", "active"),
            },
        )
    _sync_fts(conn, doc["id"])


def _sync_fts(conn, document_id: str) -> None:
    """为指定文书的全部条款重建 FTS 索引（rowid 与 legal_provision 对齐）。"""
    conn.execute("DELETE FROM legal_provision_fts WHERE document_id = ?", (document_id,))
    rows = conn.execute(
        "SELECT rowid, content, article_no, article_seq FROM legal_provision WHERE document_id = ?",
        (document_id,),
    ).fetchall()
    conn.executemany(
        "INSERT INTO legal_provision_fts (rowid, content, article_no, document_id, article_seq)"
        " VALUES (?, ?, ?, ?, ?)",
        [(r["rowid"], char_tokenize(r["content"]), r["article_no"], document_id, r["article_seq"])
         for r in rows],
    )


def doc_exists(conn, document_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM legal_document WHERE id = ?", (document_id,)
    ).fetchone() is not None


def get_document(conn, document_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM legal_document WHERE id = ?", (document_id,)).fetchone()
    return dict(row) if row else None


def get_versions(conn, document_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM document_version WHERE document_id = ? ORDER BY created_at", (document_id,)
    )]


def get_provisions(conn, document_id: str, version_id: str | None = None) -> list[dict]:
    if version_id:
        rows = conn.execute(
            "SELECT * FROM legal_provision WHERE document_id = ? AND version_id = ? ORDER BY article_seq",
            (document_id, version_id),
        )
    else:
        rows = conn.execute(
            "SELECT * FROM legal_provision WHERE document_id = ? ORDER BY article_seq", (document_id,)
        )
    return [dict(r) for r in rows]
