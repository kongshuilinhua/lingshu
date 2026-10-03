#!/usr/bin/env python3
"""查询翻译效果演示脚本。

用真实 API key(.env)在内存中建几个英文文档 chunk，对比：
1. 翻译前：中文查询→jieba 分词→BM25 scores（近乎 0）
2. 翻译后：中文查询→LLM 翻译成英文→BM25 scores（有效命中）
3. Dense 路：bge-m3 跨语言效果对比

用法：
    cd /home/pc/projects/lingshu
    uv run python scripts/demo_translate.py
"""

from __future__ import annotations

import os
import sys

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import get_settings
from core.integrations.llm import OpenAICompatibleProvider
from core.services.query_translate import translate_query
from core.services.rag import _tokenize, _BM25_INDEX_CACHE


# ── 模拟英文文档 chunk ────────────────────────────────────────────

SAMPLE_CHUNKS = [
    {
        "id": "chunk_1",
        "text": "Redis SET command stores a string value associated with a key. "
                "The SET command supports options like EX (expire in seconds), PX (expire in milliseconds), "
                "NX (only set if key does not exist), and XX (only set if key already exists).",
        "knowledge_base_id": 1,
        "workspace_id": 1,
        "document_id": 1,
    },
    {
        "id": "chunk_2",
        "text": "Redis persistence can be configured using RDB (snapshot) or AOF (append-only file). "
                "RDB creates point-in-time snapshots of the dataset at specified intervals. "
                "AOF logs every write operation and can replay on restart for durability.",
        "knowledge_base_id": 1,
        "workspace_id": 1,
        "document_id": 1,
    },
    {
        "id": "chunk_3",
        "text": "FastAPI dependency injection uses Depends() to declare shared logic. "
                "Sub-dependencies form a directed acyclic graph resolved top-down. "
                "Use Annotated[Type, Depends(func)] for cleaner type hints in modern Python.",
        "knowledge_base_id": 1,
        "workspace_id": 1,
        "document_id": 1,
    },
    {
        "id": "chunk_4",
        "text": "Milvus HNSW index provides approximate nearest neighbor search with high recall. "
                "It uses a hierarchical graph structure for efficient traversal. "
                "Parameters like ef_construction and M control index quality vs memory trade-off.",
        "knowledge_base_id": 1,
        "workspace_id": 1,
        "document_id": 1,
    },
    {
        "id": "chunk_5",
        "text": "JWT (JSON Web Token) authentication uses HMAC-SHA256 for signing. "
                "The token contains header, payload, and signature segments. "
                "Algorithm confusion attacks exploit alg=none to bypass signature verification.",
        "knowledge_base_id": 1,
        "workspace_id": 1,
        "document_id": 1,
    },
]

# ── 测试用中文查询 ────────────────────────────────────────────────

TEST_QUERIES = [
    "Redis的SET命令怎么用",           # 应命中 chunk_1
    "Redis怎么配置持久化",             # 应命中 chunk_2
    "FastAPI怎么做依赖注入",           # 应命中 chunk_3
    "Milvus的HNSW索引是什么",          # 应命中 chunk_4
    "JWT认证怎么防止算法混淆攻击",      # 应命中 chunk_5
]


def _build_bm25_index(chunks: list[dict]) -> None:
    """直接往 _BM25_INDEX_CACHE 里塞一个预建索引（绕过 DB）。"""
    from rank_bm25 import BM25Okapi

    tokenized_corpus = [_tokenize(c["text"]) for c in chunks]
    rows_data = [{"id": c["id"], "text": c["text"], "tokens": _tokenize(c["text"])} for c in chunks]

    version = "demo_v1"
    cache_key = (frozenset([1]), version)
    _BM25_INDEX_CACHE.clear()
    _BM25_INDEX_CACHE[cache_key] = {
        "index": BM25Okapi(tokenized_corpus),
        "rows": rows_data,
        "version": version,
    }


def _bm25_score_direct(query: str, chunks: list[dict]) -> list[tuple[str, float]]:
    """直接用 BM25Okapi 评分（不走 DB），返回 [(chunk_id, score), ...] 排序后。"""
    from rank_bm25 import BM25Okapi

    tokenized_corpus = [_tokenize(c["text"]) for c in chunks]
    bm25 = BM25Okapi(tokenized_corpus)
    tokenized_query = _tokenize(query)
    scores = bm25.get_scores(tokenized_query)
    results = [(chunks[i]["id"], float(scores[i])) for i in range(len(chunks))]
    return sorted(results, key=lambda x: x[1], reverse=True)


def main():
    print("=" * 70)
    print("  查询翻译效果演示")
    print("  对比：中文查询直接 BM25 vs 翻译后 BM25")
    print("=" * 70)

    settings = get_settings()
    print(f"\n  LLM 端点: {settings.openai_api_base}")
    print(f"  对话模型: {settings.openai_model}")
    print(f"  翻译模型: {settings.translate_model or '(复用主模型)'}")
    print(f"  Embedding: {settings.openai_embedding_model}")
    print(f"  Mock LLM: {settings.mock_llm}")

    if settings.mock_llm:
        print("\n⚠️  LINGSHU_MOCK_LLM=true，翻译和 embedding 都走 mock，无法演示真实效果。")
        print("   请设置真实 API key 后重试：")
        print("   LINGSHU_MOCK_LLM=false uv run python scripts/demo_translate.py")
        return

    provider = OpenAICompatibleProvider()

    for query in TEST_QUERIES:
        print(f"\n{'─' * 70}")
        print(f"  中文查询: {query}")

        # 1. BM25 直接用中文查询（翻译前）
        scores_before = _bm25_score_direct(query, SAMPLE_CHUNKS)
        max_before = scores_before[0][1]
        print("\n  [翻译前] BM25 直接用中文查询:")
        for cid, score in scores_before:
            bar = "█" * int(score * 20) if score > 0 else ""
            print(f"    {cid:10} score={score:8.4f} {bar}")

        if max_before <= 0:
            print("    ⚠️ 所有 chunk 得分为 0 — BM25 跨语言哑火！")

        # 2. 翻译
        translated = translate_query(provider, query)
        if translated:
            print(f"\n  [翻译] → {translated}")
            # 3. BM25 用英文查询（翻译后）
            scores_after = _bm25_score_direct(translated, SAMPLE_CHUNKS)
            max_after = scores_after[0][1]
            print("\n  [翻译后] BM25 用英文查询:")
            for cid, score in scores_after:
                bar = "█" * int(score * 20) if score > 0 else ""
                print(f"    {cid:10} score={score:8.4f} {bar}")

            # 4. 对比
            top_before = scores_before[0][0]
            top_after = scores_after[0][0]
            print(f"\n  [对比] Top-1: 翻译前={top_before}(score={max_before:.4f}) → 翻译后={top_after}(score={max_after:.4f})")
            if max_after > max_before:
                print(f"  ✅ BM25 召回恢复：score 从 {max_before:.4f} 提升到 {max_after:.4f}")
            else:
                print("  ➖ 无明显提升")
        else:
            print("\n  [翻译] 跳过（英文为主或翻译失败）")
            print("  ➖ 使用原始查询")

    print(f"\n{'=' * 70}")
    print("  演示完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
