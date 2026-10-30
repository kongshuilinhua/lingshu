"""单条切片增删改：改完下一次检索立刻能看到，不用整篇重切。"""


def _kb_with_doc(client, headers, text="营业时间为上午九点到晚上八点。"):
    kb = client.post("/api/knowledge-bases", headers=headers, json={"name": "切片维护库"}).json()["knowledge_base"]
    doc = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents", headers=headers, json={"filename": "a.txt", "text": text}
    ).json()["document"]
    return kb, doc


def _chunks(client, headers, kb, doc):
    return client.get(f"/api/knowledge-bases/{kb['id']}/documents/{doc['id']}/chunks", headers=headers).json()["chunks"]


def _search(client, headers, kb, query):
    body = client.post(f"/api/knowledge-bases/{kb['id']}/retrieval-test", headers=headers, json={"query": query}).json()
    return [item.get("snippet") or "" for item in body["items"]]


def test_edit_chunk_changes_retrieval(client, auth_headers):
    kb, doc = _kb_with_doc(client, auth_headers)
    chunk = _chunks(client, auth_headers, kb, doc)[0]

    response = client.patch(
        f"/api/knowledge-bases/{kb['id']}/documents/{doc['id']}/chunks/{chunk['id']}",
        headers=auth_headers,
        json={"text": "退货需在签收后七天内申请，运费由买家承担。"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["chunks"][0]["text"].startswith("退货需在签收后七天内")

    # BM25 词表与向量都要换新：用新内容的关键词能搜到，旧内容搜不到
    assert any("退货" in text for text in _search(client, auth_headers, kb, "退货运费"))
    assert not any("营业时间" in text for text in _search(client, auth_headers, kb, "营业时间"))


def test_add_and_delete_chunk(client, auth_headers):
    kb, doc = _kb_with_doc(client, auth_headers)
    before = _chunks(client, auth_headers, kb, doc)

    added = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/{doc['id']}/chunks",
        headers=auth_headers,
        json={"text": "客服电话 400-123-4567，工作日九点到十八点。"},
    )
    assert added.status_code == 200, added.text
    body = added.json()
    assert len(body["chunks"]) == len(before) + 1
    assert body["document"]["chunk_count"] == len(before) + 1
    assert any("400-123-4567" in text for text in _search(client, auth_headers, kb, "客服电话"))

    new_chunk = body["chunks"][-1]
    removed = client.delete(
        f"/api/knowledge-bases/{kb['id']}/documents/{doc['id']}/chunks/{new_chunk['id']}", headers=auth_headers
    )
    assert removed.status_code == 200
    assert len(removed.json()["chunks"]) == len(before)
    assert not any("400-123-4567" in text for text in _search(client, auth_headers, kb, "客服电话"))


def test_chunk_from_other_document_is_404(client, auth_headers):
    kb, doc = _kb_with_doc(client, auth_headers)
    _, other = _kb_with_doc(client, auth_headers, text="另一份文档。")
    other_chunk = client.get(
        f"/api/knowledge-bases/{other['knowledge_base_id']}/documents/{other['id']}/chunks", headers=auth_headers
    ).json()["chunks"][0]
    response = client.patch(
        f"/api/knowledge-bases/{kb['id']}/documents/{doc['id']}/chunks/{other_chunk['id']}",
        headers=auth_headers,
        json={"text": "越权修改"},
    )
    assert response.status_code == 404
