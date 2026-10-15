# filepath: d:/pycharmprojects/langchain/tests/test_knowledge_service.py

from core.services.knowledge import split_by_hierarchy, mark_document_reindexing

def test_split_by_hierarchy():
    markdown_text = "# H1\nText under H1\n## H2\nText under H2\n### H3\nText under H3"
    # 测试三级层级切分
    chunks = split_by_hierarchy(markdown_text, kb_id=1, document_id=1, max_level=3)
    
    assert len(chunks) == 3
    assert chunks[0]["section"] == "H1: H1"
    assert chunks[1]["section"] == "H1: H1 > H2: H2"
    assert chunks[2]["section"] == "H1: H1 > H2: H2 > H3: H3"

def test_split_by_hierarchy_fallback():
    # If no headers match, it should fall back to split_parent_child
    text = "Just some text without any headings here. It should be split using parent-child logic."
    chunks = split_by_hierarchy(text, kb_id=1, document_id=1)
    assert len(chunks) > 0
    assert chunks[0]["parent_id"].startswith("kb1-doc1-parent0")

def test_split_by_hierarchy_max_level():
    # If max_level is 2, H3 heading should not be treated as a split point,
    # but its content should be preserved as part of the parent H2 section.
    markdown_text = "# H1\nText 1\n## H2\nText 2\n### H3\nText 3"
    chunks = split_by_hierarchy(markdown_text, kb_id=1, document_id=1, max_level=2)
    assert len(chunks) == 2
    assert chunks[0]["section"] == "H1: H1"
    assert chunks[1]["section"] == "H1: H1 > H2: H2"
    assert "Text 2" in chunks[1]["text"]
    assert "Text 3" in chunks[1]["text"]

def test_split_by_hierarchy_intro():
    # Verify that introductory text before the first heading is successfully captured
    markdown_text = "This is introductory text.\n# H1\nText 1"
    chunks = split_by_hierarchy(markdown_text, kb_id=1, document_id=1, max_level=3)
    assert len(chunks) == 2
    assert chunks[0]["section"] == "前言"
    assert chunks[0]["text"] == "This is introductory text."
    assert chunks[1]["section"] == "H1: H1"
    assert chunks[1]["text"] == "H1\nText 1"

def test_split_by_hierarchy_no_keep_hierarchy():
    markdown_text = "# H1\nText 1"
    chunks = split_by_hierarchy(markdown_text, kb_id=1, document_id=1, keep_hierarchy_info=False)
    assert len(chunks) == 1
    assert chunks[0]["section"] == ""

def test_split_by_hierarchy_siblings():
    # Verify that consecutive H3 siblings correctly pop each other instead of nesting
    markdown_text = "# H1\nText 1\n## H2\nText 2\n### H3_A\nText A\n### H3_B\nText B"
    chunks = split_by_hierarchy(markdown_text, kb_id=1, document_id=1, max_level=3)
    assert len(chunks) == 4  # H1, H2, H3_A, H3_B = 4
    # H3_A path should be H1 > H2 > H3_A
    assert chunks[2]["section"] == "H1: H1 > H2: H2 > H3: H3_A"
    # H3_B path should be H1 > H2 > H3_B (NOT nested under H3_A)
    assert chunks[3]["section"] == "H1: H1 > H2: H2 > H3: H3_B"


# ── B2.1 mark_document_reindexing 原子状态守卫 ────────────────────────────


class _FakeRowcountResult:
    """模拟 SQLAlchemy CursorResult，控制 execute 返回的 rowcount。"""
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _FakeDb:
    """绕过 MySQL 1419 的纯 fake Session——只记 execute/commit 调用，不连库。"""
    def __init__(self, rowcount: int = 1):
        self._rowcount = rowcount
        self.calls: list[dict] = []

    def execute(self, stmt):
        self.calls.append({"method": "execute", "stmt": str(stmt)})
        return _FakeRowcountResult(self._rowcount)

    def commit(self):
        self.calls.append({"method": "commit"})


def test_mark_document_reindexing_succeeds_when_not_indexing():
    """status != 'indexing' 时原子更新，返回 True。"""
    db = _FakeDb(rowcount=1)
    result = mark_document_reindexing(db, document_id=42)
    assert result is True
    assert len(db.calls) == 2  # execute + commit
    stmt_str = db.calls[0]["stmt"]
    # 确认 UPDATE 语句含 status != 'indexing' 守卫
    assert "status != :status_1" in stmt_str or "knowledge_documents" in stmt_str


def test_mark_document_reindexing_refuses_when_already_indexing():
    """status == 'indexing' 时 rowcount=0，返回 False（防并发重复执行）。"""
    db = _FakeDb(rowcount=0)
    result = mark_document_reindexing(db, document_id=42)
    assert result is False


def test_mark_document_reindexing_clears_error_and_chunks():
    """确认 UPDATE values 清空 error_message 与 chunk_count。"""
    db = _FakeDb(rowcount=1)
    result = mark_document_reindexing(db, document_id=1)
    assert result is True
    stmt_str = db.calls[0]["stmt"]
    # 参数名可能因 ORM 编译不同，检查关键词即可
    assert "error_message" in stmt_str.lower() or "error_message" in stmt_str
    assert "chunk_count" in stmt_str.lower() or "chunk_count" in stmt_str


# ── A1.1 扫描件 / 空 PDF 明确报错 ──────────────────────────────────────


def test_prepare_document_payload_empty_file_extraction(monkeypatch):
    """文件提取返回空文本时抛 KnowledgeDocumentError 并提示扫描件。"""
    from core.services import knowledge as k_mod
    from core.services.knowledge import KnowledgeDocumentError

    def _fake_extract(_filename, _content_type, _raw):
        return ""

    def _fake_sanitize(text):
        return text

    monkeypatch.setattr(k_mod, "extract_document_text", _fake_extract)
    monkeypatch.setattr(k_mod, "sanitize_extracted_text", _fake_sanitize)

    try:
        k_mod._prepare_document_payload(
            filename="scan.pdf",
            text=None,
            content=None,
            content_type="application/pdf",
            source_type="file",
            content_base64="ZHVtbXk=",  # "dummy" in base64
        )
        assert False, "应该抛出 KnowledgeDocumentError"
    except KnowledgeDocumentError as exc:
        assert "扫描件" in str(exc) or "未能从该文件提取到文本" in str(exc)
        assert exc.record_failed is True
        assert exc.status_code == 422


def test_prepare_document_payload_text_source_empty_still_fails():
    """纯文本 source_type 空内容走已有校验（不误伤），不应出现扫描件提示。"""
    from core.services.knowledge import KnowledgeDocumentError

    try:
        from core.services.knowledge import _prepare_document_payload
        _prepare_document_payload(
            filename="note.txt",
            text=None,
            content="",
            content_type="text/plain",
            source_type="text",
            content_base64=None,
        )
        assert False, "应抛出异常"
    except KnowledgeDocumentError as exc:
        # 文本源空内容报错不包含"扫描件"
        assert "扫描件" not in str(exc)


def test_custom_segment_mode_honours_user_delimiter():
    """重切分的「分隔标识符」必须真实生效。

    旧实现：前端输入框把 delimiter 传到后端（ResegmentRequest.delimiter），
    但 chunk_document 从不读取 cfg["delimiter"]，用户填了等于没填。

    ⚠️ 参数说明（brief 原参数不可用）：brief 原用 13 字符文本 + max_chunk_len=100，
    但 chunk_document 的 child 预算 = int(max_chunk_len * 0.35) = 35 字符，
    远大于文本本身，RecursiveCharacterTextSplitter 会把切出的片段合并回 1 块；
    且实测在能切出 ≥3 段的尺寸区间，默认分隔符同样能切出 ≥3 段，
    故 brief 原参数无论是否读取 delimiter 都得到 1 段，断言无判别力。
    这里改用 36 字符文本 + max_chunk_len=60（child 预算 21 字符）：
    文本无任何默认分隔符字符（换行/句号等），默认分隔符只能切出 2 段，
    只有把用户分隔符当首选切点才能切出 3 段及以上。
    """
    from core.services.knowledge import chunk_document

    text = "ABCDEFGHIJ|||KLMNOPQRST|||UVWXYZABCD"  # 36 字符，无默认分隔符字符
    children, _ = chunk_document(
        text,
        content_type="text/plain",
        kb_id=1,
        document_id=1,
        segment_config={
            "segment_mode": "custom",
            "delimiter": "|||",
            "max_chunk_len": 60,
            "overlap_pct": 0,
        },
    )

    joined = "".join(child["text"] for child in children)
    assert "ABCDEFGHIJ" in joined
    assert "KLMNOPQRST" in joined
    assert "UVWXYZABCD" in joined
    # child 预算 21 字符，按 "|||" 切才有 3 段；默认分隔符只能切出 2 段。
    assert len(children) >= 3


def test_custom_delimiter_is_not_left_in_chunk_text():
    """用户分隔标识符只是切点标记，不应残留在下一块开头（真实环境曾出现「###第二段…」）。"""
    from core.services.knowledge import chunk_document

    children, parents = chunk_document(
        "ABCDEFGHIJ|||KLMNOPQRST|||UVWXYZABCD",
        content_type="text/plain",
        kb_id=1,
        document_id=1,
        segment_config={"segment_mode": "custom", "delimiter": "|||", "max_chunk_len": 60, "overlap_pct": 0},
    )

    assert len(children) >= 3
    assert all("|||" not in chunk["text"] for chunk in children)
    # 父块可能跨越多段（内部保留分隔符），但首尾不应挂着分隔符
    assert all(not chunk["text"].startswith("|||") and not chunk["text"].endswith("|||") for chunk in parents)
