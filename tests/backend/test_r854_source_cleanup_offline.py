"""Check that multipart cleanup drains all spools even when one close fails."""

from backend.agent.document_workflow import run_document_workflow
from backend.multimodal import document_transport
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_docx, sample_pdf, save_office
from test_r848_document_agent_lease_offline import PASSAGE, Upload, app_engine, host  # noqa: F401


def test_close_failure_attempts_remaining_spools_and_withholds_evidence(host, app_engine, monkeypatch, caplog):
    files = [('report.pdf', sample_pdf()), ('notes.docx', save_office(sample_docx()))]
    upload = Upload(host, files)
    cleanups = []
    agent_calls = []
    close_parser = document_transport._Parts.close

    def close_with_failure(parser):
        first, second = parser._files_to_close_on_error
        close_first = first.close

        def broken_close():
            close_first()
            raise OSError('private spool diagnostic')

        with monkeypatch.context() as fault:
            fault.setattr(first, 'close', broken_close)
            try:
                close_parser(parser)
            finally:
                cleanups.append((first.closed, second.closed, not parser.items,
                                 not parser._files_to_close_on_error, not parser._file_parts_to_write,
                                 not parser._file_parts_to_finish))
                second.close()

    def unexpected_task(*args, **kwargs):
        agent_calls.append(True)
        raise AssertionError('Agent invoked after failed spool cleanup')

    monkeypatch.setattr(document_transport._Parts, 'close', close_with_failure)
    monkeypatch.setattr(app_engine, 'run_document_task', unexpected_task)

    async def run():
        await upload.authenticate()
        terminal, = await run_document_workflow(
            upload.request, 'a-conversation', PASSAGE, source_authority=lambda *_: upload.grants,
        )
        assert terminal['type'] == 'task_failed' and terminal['error_code'] == 'OPERATION_UNAVAILABLE'
        assert terminal['partial'] is None and 'answer' not in terminal
        assert 'private spool diagnostic' not in terminal['error']
        assert not document_transport.has_active_transport()

    run_offline(run())
    assert 'private spool diagnostic' not in caplog.text
    assert agent_calls == []
    assert cleanups == [(True, True, True, True, True, True)]
