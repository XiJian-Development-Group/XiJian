"""记忆语义检索升级（2026-08-09）测试。

覆盖三条路径：
  a) 有 embedding 后端（桩）时 create 写入 embedding 字段，
     recall_search 向量命中排序优先；
  b) embedding 后端不可用时降级为关键词检索且不报错；
  c) update 改 content 时重算向量；后端不可用则向量置 None。
"""

from __future__ import annotations

import pytest

from xijian_api.ai.base import BackendUnavailable
from xijian_api.stubs import embedding as embedding_stub
from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import state


# ---------------------------------------------------------------------------
# 桩：确定性 embedding 后端
# ---------------------------------------------------------------------------


class _FakeEmbeddingBackend:
    """Deterministic test backend：按内容子串映射到 3 维单位向量。

    含「查询」的文本映射到 [1,0,0]（查询轴）；语义相关的条目文本
    映射到相邻轴 [0.9,0.1,0]（与查询 cosine ≈ 0.99）；无关文本映射
    到正交轴（cosine = 0），关键词不重叠也能由语义命中。
    """

    #: (子串, 向量) —— 有序，先命中优先。
    _RULES = [
        ("查询", [1.0, 0.0, 0.0]),    # 查询轴
        ("宇宙", [0.9, 0.1, 0.0]),    # 与查询轴近邻
        ("语义无关", [0.0, 1.0, 0.0]),  # 与查询轴正交
        ("其他话题", [0.0, 0.0, 1.0]),  # 与查询轴正交
    ]

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def is_available(self) -> bool:
        return True

    def embed(self, texts, *, model_id=None) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for text in texts:
            vec = [0.0, 0.0, 0.0]
            for key, v in self._RULES:
                if key in text:
                    vec = list(v)
                    break
            out.append(vec)
        return out


@pytest.fixture
def fake_embedding(monkeypatch):
    """把 registry 的 ``get_embedding_backend`` 换成桩，返回桩实例。"""
    backend = _FakeEmbeddingBackend()
    monkeypatch.setattr(embedding_stub, "get_embedding_backend", lambda *a, **kw: backend)
    return backend


@pytest.fixture
def no_embedding(monkeypatch):
    """模拟后端不可用：registry 抛 ``BackendUnavailable``（→ 503）。"""

    def _boom(*a, **kw):
        raise BackendUnavailable("no usable backend for embeddings")

    monkeypatch.setattr(embedding_stub, "get_embedding_backend", _boom)


# ---------------------------------------------------------------------------
# c) create 时成功写入 embedding 字段
# ---------------------------------------------------------------------------


def test_create_writes_embedding_fields(fake_embedding):
    state.memory.clear()
    record = memory_stub.create(
        {
            "character_id": "char_sem",
            "type": "short",
            "content": "关于宇宙起源的一段记录",
            "importance": 0.7,
        }
    )
    assert record["embedding"] == [0.9, 0.1, 0.0]
    # embedding_model 来自后端 envelope 的 model 字段。
    assert record["embedding_model"] == "stub-embedding"


def test_create_with_precomputed_embedding(no_embedding):
    """payload 自带向量时不再调后端，原样写入。"""
    state.memory.clear()
    record = memory_stub.create(
        {
            "character_id": "char_sem",
            "type": "short",
            "content": "预计算向量的条目",
            "embedding": [1.0, 0.0, 0.0],
            "embedding_model": "precomputed-model",
        }
    )
    assert record["embedding"] == [1.0, 0.0, 0.0]
    assert record["embedding_model"] == "precomputed-model"


# ---------------------------------------------------------------------------
# a) 向量语义命中参与排序（排序优先）
# ---------------------------------------------------------------------------


def test_recall_search_ranks_semantic_hit_first(fake_embedding):
    """语义命中（关键词不重叠）应排在低重要度的关键词命中之前。"""
    state.memory.clear()
    # 关键词命中的低重要度条目：content 恰好包含查询子串。
    keyword_hit = memory_stub.create(
        {
            "character_id": "char_sem",
            "type": "short",
            "importance": 0.2,
            "decay_score": 1.0,
            "content": "这条记录提到语义相关查询字样",
        }
    )
    # 语义命中、关键词不重叠、重要度更高：向量 [0.9,0.1,0] ≈ 查询 [1,0,0]。
    semantic_hit = memory_stub.create(
        {
            "character_id": "char_sem",
            "type": "short",
            "importance": 0.9,
            "decay_score": 1.0,
            "content": "关于宇宙起源的深层记录",
        }
    )
    # 无关条目：向量与查询正交 → cosine=0 < floor，应被排除。
    memory_stub.create(
        {
            "character_id": "char_sem",
            "type": "short",
            "importance": 0.9,
            "decay_score": 1.0,
            "content": "关于其他话题的记录",
        }
    )

    hits = memory_stub.recall_search(
        character_id="char_sem", query="语义相关查询", top_k=5
    )
    # 无关条目被语义 floor 过滤；两条命中按分数降序。
    ids = [h["entry"]["id"] for h in hits]
    assert len(hits) == 2
    assert ids[0] == semantic_hit["id"]
    assert ids[1] == keyword_hit["id"]
    # 语义命中的分数高于关键词命中（0.9*0.9 > 0.2*1.0）。
    assert hits[0]["score"] > hits[1]["score"]


def test_recall_search_semantic_only_entries(fake_embedding):
    """纯语义检索：无关键词重叠、但有向量相似的条目也能被召回。"""
    state.memory.clear()
    memory_stub.create(
        {
            "character_id": "char_sem",
            "type": "short",
            "importance": 0.8,
            "content": "宇宙大爆炸之后的世界",
        }
    )
    memory_stub.create(
        {
            "character_id": "char_sem",
            "type": "short",
            "importance": 0.8,
            "content": "语义无关的日常记录",
        }
    )
    hits = memory_stub.recall_search(
        character_id="char_sem", query="天文查询词", top_k=5
    )
    assert len(hits) == 1
    assert "宇宙" in hits[0]["entry"]["content"]


# ---------------------------------------------------------------------------
# b) 后端不可用 → 完整回退关键词，不报错
# ---------------------------------------------------------------------------


def test_recall_search_falls_back_without_backend(no_embedding):
    state.memory.clear()
    record = memory_stub.create(
        {
            "character_id": "char_sem",
            "type": "short",
            "importance": 0.9,
            "content": "用户最喜欢冰淇淋",
            "decay_score": 1.0,
        }
    )
    # 后端不可用：create 不报错，embedding 字段保持 None。
    assert record["embedding"] is None
    assert record["embedding_model"] is None

    hits = memory_stub.recall_search(
        character_id="char_sem", query="冰淇淋", top_k=5
    )
    # 关键词公式正常命中，返回结构不变。
    assert len(hits) == 1
    assert hits[0]["entry"]["id"] == record["id"]
    assert set(hits[0].keys()) == {"entry", "score"}

    # 无关键词重叠时仍返回空（与旧行为一致）。
    assert memory_stub.recall_search(
        character_id="char_sem", query="完全不相关", top_k=5
    ) == []


def test_recall_search_no_backend_env_default():
    """真实环境默认路径（无桩）：create + recall 都不报错、结构不变。

    断言只检查返回结构与降级行为，不依赖本机是否恰好装了
    embedding 依赖——若装了真实后端，条目会带上向量，但关键词
    公式仍应生效且返回结构不变。
    """
    state.memory.clear()
    memory_stub.create(
        {
            "character_id": "char_sem",
            "type": "short",
            "importance": 0.8,
            "content": "测试语义回退路径的独特内容",
        }
    )
    hits = memory_stub.recall_search(
        character_id="char_sem", query="独特内容", top_k=5
    )
    assert len(hits) == 1
    assert set(hits[0].keys()) == {"entry", "score"}


# ---------------------------------------------------------------------------
# update：content 变更时重算向量
# ---------------------------------------------------------------------------


def test_update_recomputes_embedding(fake_embedding):
    state.memory.clear()
    record = memory_stub.create(
        {
            "character_id": "char_sem",
            "type": "short",
            "content": "关于宇宙的旧记录",
        }
    )
    assert record["embedding"] == [0.9, 0.1, 0.0]

    updated = memory_stub.update(
        record["id"], {"content": "语义无关的新内容"}
    )
    # 向量随内容重算：新内容映射到正交轴。
    assert updated["embedding"] == [0.0, 1.0, 0.0]
    assert updated["embedding_model"] == "stub-embedding"


def test_update_without_backend_clears_embedding(no_embedding):
    state.memory.clear()
    record = memory_stub.create(
        {
            "character_id": "char_sem",
            "type": "short",
            "content": "内容一",
            "embedding": [1.0, 0.0, 0.0],  # 预计算向量
        }
    )
    updated = memory_stub.update(record["id"], {"content": "内容二"})
    # 后端不可用 → 无法重算，向量降级为 None（不留陈旧向量）。
    assert updated["embedding"] is None
    assert updated["embedding_model"] is None
