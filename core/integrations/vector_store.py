from __future__ import annotations

import math
import socket
import hashlib
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Iterable

from core.config import get_settings


@dataclass
class VectorHit:
    """
    统一的向量检索召回命中结果。
    """
    vector_id: str
    text: str
    score: float
    metadata: dict


class MemoryVectorStore:
    """
    轻量级进程内内存向量空间（用于开发环境调试或作为 Milvus 挂掉时的保底）。

    🎯 意图与工程大局观：
        为多租户或单元测试提供轻量化的向量仿真。不依赖任何外部进程，全内存计算，保证服务“开箱即用”。
    """

    def __init__(self) -> None:
        # 数据结构设计：列表存储 (UUID, 稠密特征向量, 原始文本, 元数据)
        self._items: list[tuple[str, list[float], str, dict]] = []

    def upsert(self, vector_id: str, vector: list[float], text: str, metadata: dict) -> None:
        """
        插入或更新内存向量。
        """
        self._items = [item for item in self._items if item[0] != vector_id]
        self._items.append((vector_id, vector, text, metadata))

    def search(self, vector: list[float], *, limit: int = 5, filters: dict | None = None) -> list[VectorHit]:
        """
        暴力检索（K-Nearest Neighbors）：
            遍历内存中所有向量，执行余弦相似度计算，排序并返回前 limit 个最优命中点。

        ⚡ 边界与性能思考：
            - 时间复杂度为 O(N * D)，N 为内存文档总块数，D 为向量维度。在大规模生产环境下（N > 10,000）内存占用与检索时延会呈线性暴涨，仅适合中小规模或本地化开发。
        """
        filters = filters or {}
        hits = []
        for vector_id, item_vector, text, metadata in self._items:
            # 🛡️ 过滤校验：内存环境下的前置 KV 元数据完全匹配
            if any(metadata.get(key) != value for key, value in filters.items()):
                continue
            hits.append(VectorHit(vector_id, text, _cosine(vector, item_vector), metadata))
        return sorted(hits, key=lambda item: item.score, reverse=True)[:limit]

    def delete(self, *, filters: dict) -> None:
        """
        从内存中剔除匹配特定过滤条件的元数据对应的向量。
        """
        self._items = [
            item
            for item in self._items
            if not all(item[3].get(key) == value for key, value in filters.items())
        ]


class MilvusVectorStore:
    """
    企业级 Milvus 向量存储引擎包装类。

    🎯 意图与工程大局观：
        本类承载了平台大规模 RAG 系统中最核心的知识片段检索与写入。
        核心设计哲学是**“平滑降级（Graceful Degradation）”**：
        当在启动或运行中检测到 Milvus 连接超时或断开时，系统会自动将写入与搜索中继切流到内存 `MemoryVectorStore` 保底，
        确保平台在无基础设施的开发环境下，或向量库单点故障下，聊天业务仍然是高可用的。
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._fallback = MemoryVectorStore()
        self._client = None
        self._error = ""
        if self.settings.vector_backend == "milvus":
            try:
                from pymilvus import MilvusClient

                # 🛡️ 防御性前置嗅探：防止由于连接卡死或超时导致应用启动进程被硬阻塞
                self._probe_milvus_endpoint()
                
                # 🧠 魔鬼数字与前沿技术参数：
                # 设置 `timeout=2` (秒)。向量数据库连接超时不可设得过长，高并发启动下需尽快决断是否触发降级。
                self._client = MilvusClient(uri=self.settings.milvus_uri, token=self.settings.milvus_token, timeout=2)
                self._client.list_collections(timeout=2)
            except Exception as exc:
                self._client = None
                self._error = str(exc)[:240]

    @property
    def available(self) -> bool:
        """向量存储子系统是否处于可用就绪状态。"""
        return self.settings.vector_backend != "milvus" or self._client is not None

    @property
    def using_fallback(self) -> bool:
        """是否已触发降级保底。"""
        return self.settings.vector_backend == "milvus" and self._client is None

    def status(self) -> dict:
        """对外暴露给健康检查 API（deps.py）的状态看板。"""
        return {
            "backend": self.settings.vector_backend,
            "configured_backend": self.settings.vector_backend,
            "active_backend": "milvus" if self._client else "memory",
            "collection": self.settings.milvus_collection if self.settings.vector_backend == "milvus" else None,
            "uri": self.settings.milvus_uri if self.settings.vector_backend == "milvus" else None,
            "available": self.available,
            "fallback": self.using_fallback,
            "error": self._error or None,
        }

    def upsert(self, vector_id: str, vector: list[float], text: str, metadata: dict) -> None:
        """
        往向量库中插入/更新单条向量及其元数据实体。
        """
        if not self._client:
            self._fallback.upsert(vector_id, vector, text, metadata)
            return
        self._ensure_collection(len(vector))
        # 🧠 魔鬼数字：把字符串 vector_id 映射成 int64，因为 Milvus 高效主键对 int64 的内存检索与聚簇索引性能极高。
        payload = {"id": _int64_id(vector_id), "vector": vector, "text": text, "vector_id": vector_id, **metadata}
        self._client.upsert(collection_name=self.settings.milvus_collection, data=[payload])

    def search(self, vector: list[float], *, limit: int = 5, filters: dict | None = None) -> list[VectorHit]:
        """
        向量库语义近似性检索（RAG 稠密召回第一阶段）。
        """
        if not self._client:
            return self._fallback.search(vector, limit=limit, filters=filters)
        self._ensure_collection(len(vector))
        expr = build_milvus_filter(filters or {})
        results = self._client.search(
            collection_name=self.settings.milvus_collection,
            data=[vector],
            limit=limit,
            filter=expr or "",
            # 🛡️ 安全设计与性能考虑：
            # 必须指定输出的 Schema 元数据字段白名单。
            # 大规模召回中，禁止将巨大的 `vector` 特征数组回传回来（会浪费极大的网卡 I/O 并引发内存抖动），只需返回索引和文本引用。
            output_fields=[
                "text",
                "vector_id",
                "workspace_id",
                "knowledge_base_id",
                "document_id",
                "chunk_id",
                "parent_id",
                "filename",
                "title",
                "page",
                "section",
                "content_hash",
            ],
        )
        hits = []
        for item in results[0]:
            entity = item.get("entity", {})
            hits.append(
                VectorHit(
                    vector_id=str(item.get("id")),
                    text=entity.get("text", ""),
                    score=float(item.get("distance", 0)),
                    metadata={key: value for key, value in entity.items() if key != "text"},
                )
            )
        return hits

    def delete(self, *, filters: dict) -> None:
        """
        根据指定的过滤表达式删除指定向量。
        """
        if not self._client:
            self._fallback.delete(filters=filters)
            return
        # collection 不存在时直接跳过——全新 Milvus 上没有东西可删，
        # 不应因 collection 未创建（首次入库 delete 先于 upsert）而抛异常。
        if not self._client.has_collection(self.settings.milvus_collection):
            return
        expr = build_milvus_filter(filters)
        if expr:
            self._client.delete(collection_name=self.settings.milvus_collection, filter=expr)

    def _ensure_collection(self, vector_dimension: int) -> None:
        """
        🛡️ 防御性设计：延迟懒初始化 Collection。
        首次向量写入或检索时，若目标 Collection 不存在，根据向量实际的维度自动触发物理 Collection 与相应 HNSW/IVF 索引结构的建表。
        """
        if not self._client:
            return
        if self._client.has_collection(self.settings.milvus_collection):
            return
        dimension = self.settings.milvus_dimension or vector_dimension
        self._client.create_collection(
            collection_name=self.settings.milvus_collection,
            dimension=dimension,
            auto_id=False,
        )

    def _probe_milvus_endpoint(self) -> None:
        """
        🛡️ 坚固前置网络探测哨兵。

        🎯 意图与工程大局观：
            - PyMilvus 原生连接可能在 TCP 握手阶段陷入长期系统底层 Block。
            - 本函数在实例化 PyMilvus 之前，使用纯 Socket 对 URI 的 IP 和 Port 进行极短的 1 秒 TCP 连接探路。
            - 探路失败直接抛出异常触发降级，将超时拦截提前到应用软件层，最大程度平抑云原生环境下的网络剧烈抖动。
        """
        parsed = urlparse(self.settings.milvus_uri)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 19530)
        if not host:
            raise ValueError("MILVUS_URI is invalid")
        with socket.create_connection((host, port), timeout=1):
            return


def build_milvus_filter(filters: dict) -> str:
    """
    将普通的 Python 字典过滤结构转换为符合 Milvus ANTLR 语法规范的高级布尔代数过滤表达式。
    
    🛡️ 防御性设计：
        - 对 String 类型的过滤属性，进行转义处理，防止外部输入触发非法的语法破坏或字符过滤注入。
    """
    parts = []
    for key, value in filters.items():
        if isinstance(value, str):
            safe = value.replace('"', '\\"')
            parts.append(f'{key} == "{safe}"')
        else:
            parts.append(f"{key} == {value}")
    return " and ".join(parts)


def _cosine(left: Iterable[float], right: Iterable[float]) -> float:
    """
    手工实现高性能 Cosine 相似度计算（余弦夹角计算）。
    
    🛡️ 防御性编程：
        对模长为 0 的异常向量，默认除数设为 1.0，彻底避免除零错误引发进程挂起。
    """
    left_values = list(left)
    right_values = list(right)
    numerator = sum(a * b for a, b in zip(left_values, right_values))
    left_norm = math.sqrt(sum(a * a for a in left_values)) or 1.0
    right_norm = math.sqrt(sum(b * b for b in right_values)) or 1.0
    return numerator / (left_norm * right_norm)


def _int64_id(value: str) -> int:
    """
    通过 SHA256 哈希映射将 UUID 字符串压缩为标准的 signed int64。
    因为 Milvus 高性能散列要求主键为 BigInt。
    """
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False) & ((1 << 63) - 1)


# 全局单例向量存储实例，系统其他模块统一从此实例读写向量，维持连接池单例生命周期
vector_store = MilvusVectorStore()
