import pytest
import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from backend.agent.task_store import TaskStore
from backend.tools.file_write import FileWriteInput, create_file_write
from backend.auth.request_context import effective_owner


def test_existing_filename_cannot_transfer_owner(tmp_path):
    store = TaskStore(tmp_path / 'tasks.db')
    store.save_artifact('report.txt', 'synthetic-a', user_id='a')
    with pytest.raises(ValueError):
        store.save_artifact('report.txt', 'synthetic-b', user_id='b')
    assert store.get_artifact('report.txt')['user_id'] == 'a'
    assert store.get_artifact('report.txt')['path'] == 'synthetic-a'


def test_duplicate_registration_and_restart_keep_original_metadata(tmp_path):
    path = tmp_path / 'tasks.db'
    store = TaskStore(path)
    store.save_artifact('opaque.txt', 'synthetic', user_id='a', artifact_id='synthetic-id', display_name='report.txt')
    with pytest.raises(ValueError):
        store.save_artifact('opaque.txt', 'different', user_id='a', artifact_id='another')
    restored = TaskStore(path).get_artifact('opaque.txt')
    assert restored['user_id'] == 'a' and restored['path'] == 'synthetic'
    assert restored['artifact_id'] == 'synthetic-id' and restored['display_name'] == 'report.txt'


def test_legacy_artifact_metadata_is_preserved_without_assigning_id(tmp_path):
    path = tmp_path / 'tasks.db'
    conn = sqlite3.connect(path)
    try:
        conn.execute('CREATE TABLE artifacts (filename TEXT PRIMARY KEY, path TEXT NOT NULL, task_id TEXT, user_id TEXT, user_role TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, size_bytes INTEGER DEFAULT 0, sha256_hash TEXT)')
        conn.execute("INSERT INTO artifacts(filename,path,user_id,created_at,updated_at) VALUES ('legacy.txt','synthetic','a','2026','2026')")
        conn.commit()
    finally:
        conn.close()
    record = TaskStore(path).get_artifact('legacy.txt')
    assert record['user_id'] == 'a' and record['path'] == 'synthetic'
    assert record['artifact_id'] is None and record['display_name'] is None


def test_concurrent_metadata_registration_cannot_transfer_owner(tmp_path):
    path = tmp_path / 'tasks.db'
    TaskStore(path)
    def register(owner):
        try:
            TaskStore(path).save_artifact('same.txt', 'synthetic', user_id=owner)
            return 'created', owner
        except ValueError:
            return 'denied', owner
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, ['a', 'b']))
    assert sorted(result[0] for result in results) == ['created', 'denied']
    winner = next(owner for status, owner in results if status == 'created')
    assert TaskStore(path).get_artifact('same.txt')['user_id'] == winner


def test_overwrite_flag_cannot_replace_existing_file(tmp_path):
    from test_a21_conversation_offline import immediate
    target = tmp_path / 'report.txt'
    target.write_text('owner a synthetic content')
    execute = create_file_write(tmp_path)
    result = immediate(execute(FileWriteInput(filename='report.txt', content='owner b synthetic content', overwrite=True)))
    assert target.read_text() == 'owner a synthetic content'
    assert result['filename'] != target.name


def test_same_display_name_yields_distinct_files_and_content(tmp_path):
    from test_a21_conversation_offline import immediate
    execute = create_file_write(tmp_path)
    first = immediate(execute(FileWriteInput(filename='report.txt', content='synthetic content A')))
    second = immediate(execute(FileWriteInput(filename='report.txt', content='synthetic content B')))
    assert first['artifact_id'] != second['artifact_id']
    assert first['display_name'] == second['display_name'] == 'report.txt'
    assert (tmp_path / first['filename']).read_text() == 'synthetic content A'
    assert (tmp_path / second['filename']).read_text() == 'synthetic content B'


def test_concurrent_creations_have_distinct_storage_and_integrity(tmp_path):
    def write(i):
        return asyncio.run(create_file_write(tmp_path)(FileWriteInput(filename='report.txt', content=f'synthetic content {i}')))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write, range(8)))
    assert len({r['artifact_id'] for r in results}) == 8
    for i, result in enumerate(results):
        assert (tmp_path / result['filename']).read_text() == f'synthetic content {i}'


@pytest.mark.asyncio
async def test_document_and_workbook_collision_do_not_overwrite(tmp_path):
    from backend.tools.docx_create import DocxCreateInput, create_docx_create
    from backend.tools.xlsx_report import XlsxReportInput, create_xlsx_report
    doc = create_docx_create(tmp_path)
    sheet = create_xlsx_report(tmp_path)
    first_doc = await doc(DocxCreateInput(filename='report.docx', title='Synthetic', content='Synthetic content A'))
    second_doc = await doc(DocxCreateInput(filename='report.docx', title='Synthetic', content='Synthetic content B'))
    first_sheet = await sheet(XlsxReportInput(filename='report.xlsx', title='Synthetic report', headers=['Name'], rows=[['A']]))
    second_sheet = await sheet(XlsxReportInput(filename='report.xlsx', title='Synthetic report', headers=['Name'], rows=[['B']]))
    assert first_doc['artifact_id'] != second_doc['artifact_id']
    assert first_sheet['artifact_id'] != second_sheet['artifact_id']
    import docx, openpyxl
    assert 'Synthetic content A' in '\n'.join(p.text for p in docx.Document(tmp_path / first_doc['filename']).paragraphs)
    book = openpyxl.load_workbook(tmp_path / first_sheet['filename'], read_only=True)
    try:
        assert any('A' in [cell.value for cell in row] for row in book.active.iter_rows())
    finally:
        book.close()


@pytest.mark.asyncio
async def test_traversal_input_only_creates_opaque_file_inside_root(tmp_path):
    root = tmp_path / 'sandbox'
    result = await create_file_write(root)(FileWriteInput(filename='../report.txt', content='synthetic content'))
    assert (root / result['filename']).is_file()
    assert not (tmp_path / 'report.txt').exists()


@pytest.mark.asyncio
async def test_registration_failure_reports_failure_without_deleting_file(tmp_path, monkeypatch):
    from backend.config import settings
    from backend.tools.registry import ToolDefinition, ToolRegistry
    monkeypatch.setattr(settings, 'sandbox_dir', tmp_path)
    store = MagicMock()
    store.get_artifact.return_value = None
    store.save_artifact.side_effect = OSError('synthetic failure')
    registry = ToolRegistry()
    registry.set_task_store(store)
    registry.register(ToolDefinition(name='file_write', description='synthetic', input_schema=FileWriteInput,
                                     execute_fn=create_file_write(tmp_path)))
    token = effective_owner.set('a')
    try:
        result = await registry.execute('file_write', {'filename': 'report.txt', 'content': 'synthetic content'}, user_id='a', user_role='admin')
    finally:
        effective_owner.reset(token)
    assert not result.success and 'registration failed' in result.error
    assert len(list(tmp_path.glob('*.txt'))) == 1


@pytest.mark.asyncio
async def test_interrupted_write_does_not_replace_existing_content(tmp_path, monkeypatch):
    import os
    existing = tmp_path / 'report.txt'
    existing.write_text('original synthetic content')
    monkeypatch.setattr(os, 'fsync', lambda *_: (_ for _ in ()).throw(OSError('synthetic interruption')))
    with pytest.raises(OSError):
        await create_file_write(tmp_path)(FileWriteInput(filename='report.txt', content='new synthetic content'))
    assert existing.read_text() == 'original synthetic content'
    assert len(list(tmp_path.glob('*.txt'))) == 2


@pytest.mark.asyncio
async def test_creation_failure_prevents_metadata_registration(tmp_path, monkeypatch):
    from backend.tools.registry import ToolDefinition, ToolRegistry
    from backend.tools import artifact_storage
    store = MagicMock()
    store.get_artifact.return_value = None
    registry = ToolRegistry()
    registry.set_task_store(store)
    registry.register(ToolDefinition(name='file_write', description='synthetic', input_schema=FileWriteInput,
                                     execute_fn=create_file_write(tmp_path)))
    monkeypatch.setattr(artifact_storage.os, 'open', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('synthetic')))
    token = effective_owner.set('a')
    try:
        result = await registry.execute('file_write', {'filename': 'report.txt', 'content': 'synthetic content'}, user_id='a', user_role='admin')
    finally:
        effective_owner.reset(token)
    assert not result.success
    store.save_artifact.assert_not_called()


@pytest.mark.asyncio
async def test_symlink_storage_root_rejected(tmp_path):
    actual = tmp_path / 'actual'
    actual.mkdir()
    link = tmp_path / 'link'
    try:
        link.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip('Creating directory symlinks requires local privilege')
    with pytest.raises(ValueError, match='symlink'):
        await create_file_write(link)(FileWriteInput(filename='report.txt', content='synthetic content'))
    assert not list(actual.iterdir())


@pytest.mark.asyncio
async def test_orphan_candidates_are_reported_without_touching_legacy_files(tmp_path):
    from backend.tools.artifact_storage import identify_orphan_candidates
    execute = create_file_write(tmp_path)
    registered = await execute(FileWriteInput(filename='one.txt', content='registered synthetic content'))
    orphan = await execute(FileWriteInput(filename='two.txt', content='orphan synthetic content'))
    legacy = tmp_path / 'legacy.txt'
    legacy.write_text('legacy synthetic content')
    assert identify_orphan_candidates(tmp_path, [registered['filename']]) == [orphan['filename']]
    assert legacy.read_text() == 'legacy synthetic content'
    assert (tmp_path / orphan['filename']).is_file()
