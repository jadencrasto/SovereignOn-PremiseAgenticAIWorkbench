"""Offline RAG foundations: synthetic inputs, no clients, services or sockets."""
import asyncio
import io
from unittest.mock import AsyncMock

import httpx
import pytest
from docx import Document as DocxDocument

from backend.rag.ingest import DocumentParser, TextChunker
from backend.rag.embeddings import EmbeddingService, validate_embedding
from backend.security.provenance_foundation import document_content_allowed
from backend.rag.store import persistent_security_guarantees_available
from backend.tools.document_search import create_document_search, DocumentSearchInput


def immediate(coro):
    try:
        try:
            coro.send(None)
        except StopIteration as result:
            return result.value
        raise AssertionError('unexpected asynchronous suspension')
    finally:
        coro.close()


def test_text_hash_and_collision_after_prefix():
    first = DocumentParser.parse('sample.txt', b'a' * 1024 + b'one')
    second = DocumentParser.parse('sample.txt', b'a' * 1024 + b'two')
    assert first.document_id != second.document_id
    assert first.metadata['content_sha256'] != second.metadata['content_sha256']
    assert DocumentParser.parse('sample.txt', b'a' * 1024 + b'one') == first
    assert first.filename == 'sample.txt'


def test_docx_and_pdf_extract_synthetic_text():
    doc = DocxDocument(); doc.add_paragraph('Synthetic pump description')
    out = io.BytesIO(); doc.save(out)
    assert 'Synthetic pump description' in DocumentParser.parse('sample.docx', out.getvalue()).text
    import fitz
    pdf = fitz.open(); page = pdf.new_page(); page.insert_text((72, 72), 'Synthetic pump description')
    data = pdf.tobytes(); pdf.close()
    assert 'Synthetic pump description' in DocumentParser.parse('sample.pdf', data).text


@pytest.mark.parametrize('name,data', [('x.pdf', b'not pdf'), ('x.docx', b'not zip'), ('x.txt', b'%PDF-fake'), ('x.txt', b'\x00bad'), ('x.exe', b'data'), ('x.txt', b''), ('x.txt', b'   ')])
def test_bad_documents_rejected(name, data):
    with pytest.raises(ValueError): DocumentParser.parse(name, data)


def test_limits_and_ordered_bounded_overlap():
    with pytest.raises(ValueError): DocumentParser.parse('x.txt', b'x' * (DocumentParser.MAX_INPUT_BYTES + 1))
    doc = DocumentParser.parse('sample.txt', b'abcdefghijklmnopqrstuvwxyz')
    chunks = TextChunker(10, 3).chunk(doc)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text) <= 10 for c in chunks)
    assert chunks[0].text[-3:] == chunks[1].text[:3]
    assert [c.chunk_id for c in chunks] == [c.chunk_id for c in TextChunker(10, 3).chunk(doc)]
    assert all('page' not in c.metadata for c in TextChunker(10, 3).chunk(DocumentParser.parse('x.txt', b'[Page 45] synthetic')))
    with pytest.raises(ValueError): TextChunker(10, -1)


@pytest.mark.parametrize('value', [[], [float('nan')], [float('inf')], [True], ['1'], None])
def test_invalid_vectors(value):
    with pytest.raises(RuntimeError): validate_embedding(value)


def service(responses):
    obj = EmbeddingService.__new__(EmbeddingService)
    obj._dimensions = None; obj._model = 'synthetic'; obj._base_url = 'unused'
    obj._client = type('Client', (), {})()
    obj._client.post = AsyncMock(side_effect=responses)
    return obj


def response(data):
    return httpx.Response(200, json=data, request=httpx.Request('POST', 'http://test.invalid'))


def test_provider_batch_checks_dimensions_and_fails_atomically():
    obj = service([response({'embedding': [1, 2]}), response({'embedding': [3]})])
    with pytest.raises(RuntimeError, match='dimensionality'): immediate(obj.embed_many(['one', 'two']))
    assert obj._client.post.await_count == 2
    good = service([response({'embedding': [1, 2]}), response({'embedding': [3, 4]})])
    assert immediate(good.embed_many(['one', 'two'])) == [[1., 2.], [3., 4.]]


def test_provider_timeout_invalid_and_cancellation():
    with pytest.raises(RuntimeError, match='timed out'):
        immediate(service([httpx.ReadTimeout('synthetic')]).embed('x'))
    with pytest.raises(RuntimeError): immediate(service([response({'embedding': 'bad'})]).embed('x'))
    with pytest.raises(asyncio.CancelledError): immediate(service([asyncio.CancelledError()]).embed('x'))


def test_security_gates_remain_closed():
    assert not document_content_allowed()
    assert not persistent_security_guarantees_available()
    class ForbiddenRetriever:
        async def retrieve(self, *args, **kwargs): raise AssertionError('retrieval must not run')
    with pytest.raises(PermissionError, match='unavailable'):
        immediate(create_document_search(ForbiddenRetriever())(DocumentSearchInput(query='x')))
