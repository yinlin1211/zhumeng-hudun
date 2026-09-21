"""检索模块。

检索方案（归一化 + 召回 + 过滤 + 重排 四段式）:
1. 归一化: 把农民工口语映射为法言法语（app/normalize.py），检索与判定共用；
   展示给用户的仍是原话。口语长句的关键难点在于用词与法条几乎不重合，
   不归一化则召回和覆盖率都会失效。
2. 召回:  FTS5 全文检索，三通道依次降级——
   领域法律词精确召回（"加班费"/"社会保险"等多字词，精度最高）；
   短语召回（整句子串）；
   单字 OR 宽召回（容忍同义/口语化表述，如“双倍工资”命中“二倍工资”）。
3. 过滤:  只保留“现行有效(active)”文书的条款 —— 时效性强制约束（SQL 层）。
4. 重排:  Python 端以“实义字符覆盖率”为主、命中法律词为辅、效力位阶为末，
   克服 BM25 在中文宽匹配下排序不可靠的问题；同时输出 coverage 供智能体做范围判定。
5. 返回:  携带完整出处元数据（文书名称/条款编号/生效日期/修订日期/效力状态）。
"""

from . import config, db, normalize

# 停用字口径统一在 normalize.STOP_CHARS，检索与判定共用一套
STOP_CHARS = normalize.STOP_CHARS


def _query_chars(question: str) -> list[str]:
    """抽取查询中的实义字符（保序去重，口径见 normalize.query_chars）。"""
    return normalize.query_chars(question)


def search(conn, query: str, top_k: int | None = None, active_only: bool = True,
           doc_types: list[str] | None = None) -> list[dict]:
    """核心检索入口。返回按相关度排序的条款列表。

    每项: {document_id, document_name, doc_type, article_no, article_seq, content,
           score, coverage, status, enacted_date, revision_date}
    """
    top_k = top_k or config.RETRIEVE_TOP_K
    # 口语归一化：检索与判定统一用归一化文本（给用户展示的仍是原话）
    norm = normalize.to_legal(query) or query
    # 候选池放宽：命中“工伤/工资”这类高频词时，若池子太小会把真正对口的条文
    # 在 BM25 截断处丢掉。全库仅数百条，放宽成本可忽略。
    pool = max(top_k * 40, 800)

    # 1) 领域法律词精确召回（归一化后命中的多字词 + 口语词的法定表述扩展）
    #    扩展词很关键：用户说“押金”，法条写“收取财物”（《劳动合同法》第九条），
    #    不扩展则该问法只能召回到 1 条无关条文
    terms = normalize.legal_terms(norm)
    terms += [t for t in normalize.expand_terms(norm) if t not in terms]
    hits = _query(conn, _terms_expr(terms), pool, active_only, doc_types) if terms else []

    # 2) 三通道依次降级：术语通道没结果时才放宽，宁可少召回也不掺入宽匹配噪声
    if not hits:
        tokens = db.char_tokenize(norm).strip()
        for mode in ("phrase", "or"):
            hits = _query(conn, _build_match_expr(tokens, mode), pool, active_only, doc_types)
            if hits:
                break

    # 3) Python 端重排：实义字符覆盖率为主、命中法律词为辅、效力位阶为末
    hits = _rerank(conn, norm, hits, top_k, terms)
    # 字段归一：SQL 别名 doc_status -> status（供智能体引用标注使用）
    for h in hits:
        h["status"] = h.pop("doc_status", h.get("status", "active"))
    return hits


def _query(conn, match_expr: str, limit: int, active_only: bool,
           doc_types: list[str] | None) -> list[dict]:
    if not match_expr:
        return []
    sql = """
        SELECT p.id, p.document_id, p.article_no, p.article_seq, p.content,
               d.name AS document_name, d.doc_type, d.status AS doc_status,
               d.enacted_date, d.revision_date, d.repeal_date
        FROM legal_provision_fts fts
        JOIN legal_provision p ON p.rowid = fts.rowid
        JOIN legal_document d ON d.id = p.document_id
        WHERE legal_provision_fts MATCH ?
    """
    params: list = [match_expr]

    if active_only:
        sql += " AND d.status = 'active' AND p.status = 'active'"
    if doc_types:
        placeholders = ",".join("?" for _ in doc_types)
        sql += f" AND d.doc_type IN ({placeholders})"
        params.extend(doc_types)

    # FTS5 的 bm25() 返回负值：越相关越负，故必须 ASC
    # （此前写 DESC 会把最不相关的条文排到最前，语料扩充后尤其致命）
    sql += " ORDER BY bm25(legal_provision_fts) ASC LIMIT ?"
    params.append(limit)

    return [dict(r) for r in conn.execute(sql, params)]


def _merge(base: list[dict], more: list[dict]) -> list[dict]:
    """按条款主键合并两批召回结果（FTS 侧已按相关度排序，此处只去重）。"""
    seen = {h["id"] for h in base}
    return base + [h for h in more if h["id"] not in seen]


def _corpus_stats(conn) -> tuple[dict, dict, int]:
    """全库字形/法律词文档频率，供覆盖率与词命中率做 IDF 加权。

    动机：在劳动法语料里“用人单位/劳动者/工资”几乎每条都出现，按等权重计分时，
    这类泛化条文会靠常见字与泛词刷分，把真正对口的条文挤出候选
    （如问年休假时，含“劳动报酬+拖欠”的仲裁时效条文压过《职工带薪年休假条例》）。
    按 IDF 加权后，只有“押/薪/伤残/辞退/年休假/加班费”这类有区分度的项才真正计分。
    """
    key = tuple(conn.execute(
        "SELECT COUNT(*), IFNULL(MAX(rowid), 0) FROM legal_provision").fetchone())
    if _STATS_CACHE.get("key") == key:
        return _STATS_CACHE["df"], _STATS_CACHE["dft"], _STATS_CACHE["n"]
    df: dict[str, int] = {}
    dft: dict[str, int] = {}
    n = 0
    for (content,) in conn.execute("SELECT content FROM legal_provision"):
        n += 1
        for ch in set(content):
            df[ch] = df.get(ch, 0) + 1
        for t in normalize.term_vocab():
            if t in content:
                dft[t] = dft.get(t, 0) + 1
    _STATS_CACHE.update({"key": key, "df": df, "dft": dft, "n": n})
    return df, dft, n


_STATS_CACHE: dict = {}


def _rerank(conn, question: str, hits: list[dict], top_k: int,
            terms: list[str] | None = None) -> list[dict]:
    """以“IDF 加权的实义字符覆盖率”为主、命中法律词为辅、效力位阶为末综合排序。"""
    import math

    qchars = normalize.query_chars(question)
    qset = set(qchars)
    df, dft, n = _corpus_stats(conn) if hits else ({}, {}, 0)
    weights = {c: math.log((n + 1) / (df.get(c, 0) + 1)) for c in qset}
    denom = sum(weights.values()) or 1.0

    terms = terms or []
    tw = {t: math.log((n + 1) / (dft.get(t, 0) + 1)) for t in terms}
    tdenom = sum(tw.values()) or 1.0

    for h in hits:
        content = h["content"]
        cset = set(content)
        coverage = sum(w for c, w in weights.items() if c in cset) / denom
        # 词命中率同样按 IDF 加权：命中“年休假”比命中“劳动报酬”有价值得多
        term_ratio = sum(w for t, w in tw.items() if t in content) / tdenom if terms else 0.0
        rank_w = config.DOC_TYPE_RANK.get(h["doc_type"], 1)
        h["coverage"] = round(coverage, 4)
        h["term_ratio"] = round(term_ratio, 4)
        h["relevance"] = round(coverage * 100 + term_ratio * 40 + rank_w, 4)
        h["score"] = h["relevance"]   # 兼容智能体引用标注字段
    hits.sort(key=lambda h: (-h["relevance"], h["article_seq"]))
    return hits[:top_k]


def _terms_expr(terms: list[str]) -> str:
    """把领域法律词拼成 FTS5 短语 OR 表达式（字符索引需逐字分隔）。"""
    parts = []
    for t in terms:
        toks = db.char_tokenize(t).split()
        if toks:
            parts.append('"' + " ".join(toks) + '"')
    return " OR ".join(parts)


def _build_match_expr(q_tokens: str, mode: str = "phrase") -> str:
    """构造 FTS5 MATCH 表达式（引号/单引号/特殊字符转义处理）。"""
    parts = [t for t in q_tokens.split() if t and t not in ('"', "'", "\\")]
    if not parts:
        return ""
    if mode == "phrase":
        n = min(len(parts), 12)          # 短语模式：连续子串，最长 12 字
        return '"' + " ".join(parts[:n]) + '"'
    n = min(len(parts), 24)              # OR 模式：单字任中即召回，靠重排过滤
    return " OR ".join(parts[:n])


def hybrid_rank(query: str, results: list[dict]) -> list[dict]:
    """重排扩展点：生产环境可在此融合向量相似度得分（RRF 融合）。
    MVP 阶段覆盖率排序已含位阶加权，此函数保持原序返回。
    """
    return results


def vector_channel(embedding_func, query: str, top_k: int = 5):
    """向量检索扩展点（预留）。

    生产接入方式:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("...")
        def emb(q): return model.encode(q)
        hits = vector_channel(emb, query)
    或对接 Milvus / 云端 embedding API。MVP 默认不启用。
    """
    vec = embedding_func(query)
    # TODO: 在此将 vec 写入向量库并召回相似条款，返回与 search() 相同结构的列表
    raise NotImplementedError("向量通道需在部署环境配置 embedding 模型后启用")


def cite_of(item: dict) -> str:
    """把检索结果格式化为可追溯的引用标注文本。

    article_no 字段存储完整条款号（如“第五十条”），此处不再重复拼“第/条”。
    """
    status_tag = {"active": "现行有效", "revised": "已修订", "repealed": "已废止"}.get(
        item.get("status"), item.get("status", ""))
    parts = [f"《{item['document_name']}》{item['article_no']}",
             f"生效日期 {item.get('enacted_date') or '待核验'}"]
    if item.get("revision_date"):
        parts.append(f"最近修订 {item['revision_date']}")
    parts.append(f"效力状态 {status_tag}")
    return "；".join(parts)
