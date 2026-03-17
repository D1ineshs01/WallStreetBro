"""
xAI Collections API wrapper for RAG (Retrieval-Augmented Generation).

Allows uploading PDFs, SEC filings, and historical reports into a
server-side collection that Grok can query during scans to provide
historical context alongside real-time intelligence.
"""

import os
from typing import List, Optional

import httpx
import structlog

from config.settings import settings
from core.exceptions import CollectionsAPIError

log = structlog.get_logger(__name__)

COLLECTIONS_BASE_URL = "https://api.x.ai/v1/collections"


class GrokCollectionsClient:
    """
    Wraps the xAI Collections API.
    Use this to upload and query financial documents for RAG context.

    Typical workflow:
        client = GrokCollectionsClient()
        col_id = await client.create_collection("sec_filings_2024")
        await client.upload_document(col_id, "/path/to/10K.pdf", {"year": "2024"})
        results = await client.query_collection(col_id, "copper supply disruptions")
    """

    def __init__(self):
        self._headers = {
            "Authorization": f"Bearer {settings.xai_api_key}",
            "Content-Type": "application/json",
        }

    async def create_collection(self, name: str, description: str = "") -> str:
        """Create a new collection. Returns the collection_id."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                COLLECTIONS_BASE_URL,
                headers=self._headers,
                json={"name": name, "description": description},
                timeout=30,
            )
        if resp.status_code not in (200, 201):
            raise CollectionsAPIError(
                f"Failed to create collection '{name}': {resp.status_code} {resp.text}"
            )
        data = resp.json()
        collection_id = data.get("id") or data.get("collection_id")
        log.info("collection_created", name=name, collection_id=collection_id)
        return collection_id

    async def upload_document(
        self,
        collection_id: str,
        file_path: str,
        metadata: Optional[dict] = None,
    ) -> str:
        """
        Upload a document (PDF, DOCX, TXT) to a collection.
        Returns the document_id.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Document not found: {file_path}")

        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            content = f.read()

        upload_headers = {
            "Authorization": f"Bearer {settings.xai_api_key}",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{COLLECTIONS_BASE_URL}/{collection_id}/documents",
                headers=upload_headers,
                files={"file": (filename, content, "application/octet-stream")},
                data={"metadata": str(metadata or {})},
                timeout=120,
            )
        if resp.status_code not in (200, 201):
            raise CollectionsAPIError(
                f"Failed to upload '{filename}': {resp.status_code} {resp.text}"
            )
        data = resp.json()
        doc_id = data.get("id") or data.get("document_id")
        log.info("document_uploaded", collection_id=collection_id, filename=filename, doc_id=doc_id)
        return doc_id

    async def query_collection(
        self,
        collection_id: str,
        query: str,
        top_k: int = 5,
    ) -> List[dict]:
        """
        Run a hybrid (keyword + semantic) search against a collection.
        Returns a list of document chunks ranked by relevance.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{COLLECTIONS_BASE_URL}/{collection_id}/search",
                headers=self._headers,
                json={"query": query, "top_k": top_k},
                timeout=30,
            )
        if resp.status_code != 200:
            raise CollectionsAPIError(
                f"Collection search failed: {resp.status_code} {resp.text}"
            )
        results = resp.json().get("results", [])
        log.debug("collection_queried", collection_id=collection_id, query=query, results=len(results))
        return results

    async def list_collections(self) -> List[dict]:
        """List all available collections for this API key."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(COLLECTIONS_BASE_URL, headers=self._headers, timeout=15)
        if resp.status_code != 200:
            raise CollectionsAPIError(f"List collections failed: {resp.status_code} {resp.text}")
        return resp.json().get("collections", [])

    async def delete_collection(self, collection_id: str) -> None:
        """Delete a collection and all its documents."""
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{COLLECTIONS_BASE_URL}/{collection_id}",
                headers=self._headers,
                timeout=30,
            )
        if resp.status_code not in (200, 204):
            raise CollectionsAPIError(
                f"Delete collection failed: {resp.status_code} {resp.text}"
            )
        log.info("collection_deleted", collection_id=collection_id)

    async def get_historical_context(self, query: str, collection_id: str) -> str:
        """
        Convenience method: query a collection and return a formatted context string
        suitable for injection into a Grok prompt.
        """
        results = await self.query_collection(collection_id, query, top_k=5)
        if not results:
            return "No historical context found."

        parts = ["## Historical Context from Document Collection\n"]
        for i, chunk in enumerate(results, 1):
            text = chunk.get("text") or chunk.get("content", "")
            source = chunk.get("source") or chunk.get("document_name", "unknown")
            parts.append(f"### Source {i}: {source}\n{text}\n")

        return "\n".join(parts)
