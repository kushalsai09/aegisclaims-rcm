from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings


COLLECTION_NAME = "payer_policies"
POLICIES_PATH = Path(__file__).resolve().parent.parent / "policies"
EMBEDDING_DIMENSIONS = 384
TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)?")
SECTION_PATTERN = re.compile(r"^##\s+(?P<section>.+)$", re.MULTILINE)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyMatch:
    text: str
    payer: str
    policy_title: str
    section: str
    source: str
    distance: float


@lru_cache
def get_chroma_client() -> chromadb.PersistentClient:
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(settings.chroma_path))


def get_policy_collection() -> Collection:
    """Return the persistent policy collection without a network embedding dependency."""

    return get_chroma_client().get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
        metadata={"description": "MainStreet payer policy excerpts"},
    )


def index_policies() -> int:
    """Chunk and idempotently upsert every local payer policy into ChromaDB."""

    policy_files = sorted(POLICIES_PATH.glob("*.txt"))
    if not policy_files:
        raise FileNotFoundError(f"No policy files found in {POLICIES_PATH}")

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, str | int]] = []

    for policy_path in policy_files:
        raw_text = policy_path.read_text(encoding="utf-8").strip()
        header, sections = _parse_policy(raw_text, policy_path)
        for section, section_text in sections:
            for chunk_index, chunk in enumerate(_split_section(section_text)):
                document_id = hashlib.sha256(
                    f"{policy_path.name}:{section}:{chunk_index}:{chunk}".encode()
                ).hexdigest()
                ids.append(document_id)
                documents.append(chunk)
                metadatas.append(
                    {
                        "payer": header["payer"],
                        "policy_title": header["policy_title"],
                        "section": section,
                        "source": policy_path.name,
                        "chunk_index": chunk_index,
                    }
                )

    collection = get_policy_collection()
    existing = collection.get(include=[])["ids"]
    stale_ids = sorted(set(existing) - set(ids))
    if stale_ids:
        collection.delete(ids=stale_ids)

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embed_texts(documents),
    )
    return len(ids)


def search_policies(query: str, payer: str | None = None, limit: int = 3) -> list[PolicyMatch]:
    """Retrieve payer-specific excerpts, falling back to the full collection if necessary."""

    if limit < 1:
        raise ValueError("limit must be at least 1")

    collection = get_policy_collection()
    if collection.count() == 0:
        index_policies()

    where = {"payer": payer} if payer else None
    results = _query_collection(collection, query, limit, where)
    if not results and where:
        results = _query_collection(collection, query, limit, None)
    return results


def list_policy_chunks(limit: int = 100) -> list[dict[str, str | int]]:
    """Return indexed chunk content and metadata for administrative inspection."""

    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    collection = get_policy_collection()
    if collection.count() == 0:
        index_policies()
    result = collection.get(limit=limit, include=["documents", "metadatas"])
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    chunks: list[dict[str, str | int]] = []
    for chunk_id, document, metadata in zip(result["ids"], documents, metadatas):
        if document is None or metadata is None:
            continue
        chunks.append(
            {
                "id": chunk_id,
                "text": document,
                "payer": str(metadata["payer"]),
                "policy_title": str(metadata["policy_title"]),
                "section": str(metadata["section"]),
                "source": str(metadata["source"]),
                "chunk_index": int(metadata["chunk_index"]),
            }
        )
    return sorted(chunks, key=lambda item: (str(item["source"]), int(item["chunk_index"])))


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Create local semantic vectors, with a deterministic offline fallback."""

    try:
        embeddings = _get_semantic_embedding_function()(texts)
        return [[float(value) for value in embedding] for embedding in embeddings]
    except Exception as exc:  # Model acquisition/runtime failure must not disable local RAG.
        logger.warning("Local semantic embedding unavailable; using feature hashing: %s", exc)
        return [_embed_text_fallback(text) for text in texts]


@lru_cache
def _get_semantic_embedding_function() -> DefaultEmbeddingFunction:
    return DefaultEmbeddingFunction()


def _embed_text_fallback(text: str) -> list[float]:
    tokens = TOKEN_PATTERN.findall(text.lower())
    features = tokens + [f"{left}_{right}" for left, right in zip(tokens, tokens[1:])]
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for feature in features:
        digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, byteorder="big")
        index = value % EMBEDDING_DIMENSIONS
        sign = 1.0 if value & 1 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _parse_policy(raw_text: str, path: Path) -> tuple[dict[str, str], list[tuple[str, str]]]:
    payer_match = re.search(r"^Payer:\s*(.+)$", raw_text, re.MULTILINE)
    title_match = re.search(r"^Policy Title:\s*(.+)$", raw_text, re.MULTILINE)
    if not payer_match or not title_match:
        raise ValueError(f"Policy {path.name} must declare Payer and Policy Title headers")

    matches = list(SECTION_PATTERN.finditer(raw_text))
    if not matches:
        raise ValueError(f"Policy {path.name} must contain at least one ## section")

    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
        content = raw_text[start:end].strip()
        if content:
            sections.append((match.group("section").strip(), content))

    return {
        "payer": payer_match.group(1).strip(),
        "policy_title": title_match.group(1).strip(),
    }, sections


def _split_section(text: str) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=900,
        chunk_overlap=120,
        separators=["\n\n", "\n", ". ", " "],
        length_function=len,
    )
    return [chunk.strip() for chunk in splitter.split_text(text) if chunk.strip()]


def _query_collection(
    collection: Collection,
    query: str,
    limit: int,
    where: dict[str, str] | None,
) -> list[PolicyMatch]:
    result = collection.query(
        query_embeddings=embed_texts([query]),
        n_results=min(limit, collection.count()),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    documents = result.get("documents") or [[]]
    metadatas = result.get("metadatas") or [[]]
    distances = result.get("distances") or [[]]
    if not documents or not documents[0]:
        return []

    matches: list[PolicyMatch] = []
    for document, metadata, distance in zip(documents[0], metadatas[0], distances[0]):
        if document is None or metadata is None:
            continue
        matches.append(
            PolicyMatch(
                text=document,
                payer=str(metadata["payer"]),
                policy_title=str(metadata["policy_title"]),
                section=str(metadata["section"]),
                source=str(metadata["source"]),
                distance=float(distance),
            )
        )
    return matches


if __name__ == "__main__":
    count = index_policies()
    print(f"Indexed {count} policy chunks into '{COLLECTION_NAME}'.")
