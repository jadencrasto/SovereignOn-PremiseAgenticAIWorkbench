"""Synthetic, offline checks for non-authorizing retrieval foundations."""
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from backend.rag.grounding import select_evidence, build_context, validate_answer, answer_with_evidence
from backend.rag.retriever import Retriever, RetrievalBlocked
from backend.rag.service import DocumentService
from backend.tools.document_search import create_document_search, DocumentSearchInput


def immediate(coro):
    try:
        try: coro.send(None)
        except StopIteration as result: return result.value
        raise AssertionError('unexpected suspension')
    finally: coro.close()


def row(doc='doc_a', idx=0, distance=.1, **kw):
    return dict(document_id=doc, filename=doc+'.txt', chunk_id=f'{doc}_chunk_{idx}', chunk_index=idx,
        text='Synthetic passage', distance=distance, source_revision=1, index_version='v2', embedding_model='fake', **kw)


def select(rows, allowed=lambda d,r: True, **kw):
    options=dict(allowed=allowed,index_version='v2',embedding_model='fake',top_k=5); options.update(kw)
    return select_evidence(rows, **options)


def test_ranking_ties_topk_duplicate_and_scope():
    a=row('doc_a',0,.2); b=row('doc_b',0,.2); best=row('doc_a',1,.05)
    result=select([b,a,best,a,row('doc_private',0,.01)],allowed=lambda d,r:d!='doc_private',top_k=2)
    assert [x.chunk_id for x in result]==['doc_a_chunk_1','doc_a_chunk_0']
    assert select([b,a])[0].document_id=='doc_a'
    assert select([row(distance=.9)])==()


@pytest.mark.parametrize('change',[{'filename':''},{'document_id':''},{'chunk_id':'other'}, {'distance':float('nan')}, {'distance':float('inf')}, {'distance':-1}, {'chunk_index':-1},{'source_revision':0},{'index_version':'v1'},{'embedding_model':'other'}])
def test_bad_metadata_or_incompatible_index(change):
    value=row(); value.update(change)
    with pytest.raises(ValueError): select([value])


def test_duplicate_conflict_and_bounds():
    a=row(); b=row(); b['text']='Different'
    with pytest.raises(ValueError): select([a,b])
    with pytest.raises(ValueError): select([a],top_k=0)
    with pytest.raises(ValueError): select([a]*101)


def test_request_scoped_citations_and_all_input_dependencies():
    evidence=select([row('doc_a'),row('doc_b',distance=.2)])
    context, refs=build_context(evidence,allowed=lambda d,r:True)
    labels=list(refs)
    assert all('[' + label + ']' in context for label in labels)
    answer=validate_answer('Synthetic claim ['+labels[0]+']',refs,allowed=lambda d,r:True)
    assert answer.citations[0][1].filename=='doc_a.txt'
    assert answer.dependencies == (('doc_a',1),('doc_b',1))
    assert not hasattr(answer.citations[0][1],'page')
    with pytest.raises(ValueError): validate_answer('Invented [S3]',refs,allowed=lambda d,r:True)
    with pytest.raises(ValueError): validate_answer('No evidence',refs,allowed=lambda d,r:True)
    with pytest.raises(PermissionError): validate_answer('Synthetic ['+labels[0]+']',refs,allowed=lambda d,r:d!='doc_b')


def test_fake_grounded_pipeline_and_empty_no_generation():
    async def reply(question, context): return 'The synthetic answer '+context.split(']')[0]+']'
    generate=AsyncMock(side_effect=reply)
    answer=immediate(answer_with_evidence('Question?', [row(page=99)],allowed=lambda d,r:True,index_version='v2',embedding_model='fake',top_k=2,generate=generate,verify_evidence=lambda item: True))
    assert answer.text.endswith(']') and answer.dependencies == (('doc_a',1),)
    assert 'Synthetic passage' in generate.await_args.args[1]
    assert not hasattr(answer.citations[0][1], 'page')
    generate.reset_mock()
    assert immediate(answer_with_evidence('Question?', [],allowed=lambda d,r:True,index_version='v2',embedding_model='fake',top_k=2,generate=generate,verify_evidence=lambda item: True)) is None
    generate.assert_not_awaited()


def test_context_bound_and_cancel_propagates():
    evidence=select([row()]); context, refs=build_context(evidence,allowed=lambda d,r:True,max_chars=4)
    assert context=='' and refs=={}
    async def cancel(*args): raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        immediate(answer_with_evidence('Q',[row()],allowed=lambda d,r:True,index_version='v2',embedding_model='fake',top_k=1,generate=cancel,verify_evidence=lambda item: True))


def test_all_production_retrieval_entrypoints_are_blocked_before_access():
    store=MagicMock(); embed=MagicMock()
    with pytest.raises(RetrievalBlocked): immediate(Retriever(embed,store).retrieve('q'))
    store.count.assert_not_called(); embed.embed.assert_not_called()
    service=DocumentService.__new__(DocumentService); service._retriever=MagicMock()
    with pytest.raises(RetrievalBlocked): immediate(service.retrieve('q'))
    fake=MagicMock(); fake.retrieve=AsyncMock()
    with pytest.raises(RetrievalBlocked): immediate(create_document_search(fake)(DocumentSearchInput(query='q')))
    fake.retrieve.assert_not_awaited()

def test_citations_do_not_remap_between_answers():
    first=select([row('doc_a')]); second=select([row('doc_b')])
    _, a=build_context(first,allowed=lambda d,r:True)
    _, b=build_context(second,allowed=lambda d,r:True)
    assert set(a).isdisjoint(b)
    with pytest.raises(ValueError): validate_answer('Answer ['+next(iter(a))+']',b,allowed=lambda d,r:True)


def test_revocation_during_generation_fails_closed():
    active={'doc_a'}
    async def generate(question, context):
        active.clear()
        return 'Answer '+context.split(']')[0]+']'
    with pytest.raises(PermissionError):
        immediate(answer_with_evidence('Q',[row()],allowed=lambda d,r:d in active,index_version='v2',embedding_model='fake',top_k=1,generate=generate,verify_evidence=lambda item: True))


def test_store_query_blocked_before_touching_collection():
    from backend.rag.store import VectorStore
    store=VectorStore.__new__(VectorStore); store._collection=MagicMock()
    with pytest.raises(PermissionError): store.query([1.0])
    store._collection.query.assert_not_called()
