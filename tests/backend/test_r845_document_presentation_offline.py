"""Exercise the offline presentation contract against real synthetic document inputs."""

import json
from contextlib import aclosing
from dataclasses import replace

import pytest

from backend.multimodal import document_reasoning
from test_a1_capabilities_offline import report
from test_a22_async_offline import run_offline
from test_r842_document_extraction_offline import sample_docx, sample_pdf, save_office
from test_r843_document_reasoning_offline import table_document
from test_r844_document_agent_offline import agent, failed, run, stream, succeeded, workbook  # noqa: F401


def present(agent, question, files, **options):
    events = run(agent, question, files, present=True, **options)
    assert json.loads(json.dumps(events, allow_nan=False)) == events
    return events


def test_pdf_presentation_keeps_actual_page_warnings_and_no_other_source(agent):
    files = [('inspection.pdf', sample_pdf()), ('unselected.docx', save_office(sample_docx()))]
    answer = succeeded(present(agent, 'Find the section about pump vibration in source 1.', files))
    source, = answer['sources']
    finding, = answer['findings']
    citation, = finding['evidence']
    assert source['filename'] == 'inspection.pdf' and source['format'] == 'pdf'
    assert {'code': 'PDF_EMPTY_PAGE', 'location': {'page': 2}} in source['warnings']
    assert citation['location'] == {'page': 1} and citation['source_id'] == source['source_id']
    assert citation['value_type'] == 'text' and '7.1 mm/s' in citation['value']
    assert 'unselected.docx' not in json.dumps(answer)


def test_docx_heading_and_table_cells_use_original_coordinates_without_pdf_page(agent):
    answer = succeeded(present(agent, 'Find the section about vibration report, then calculate the average vibration.',
                               [('report.docx', save_office(sample_docx()))]))
    passage, calculation = answer['findings']
    heading, = passage['evidence']
    assert heading['location'] == {'paragraph': 1, 'body': 1} and heading['heading_level'] == 2
    reading = next(c for c in calculation['evidence'] if c['value'] == '7.1')
    assert reading['location'] == {'table': 1, 'row': 2, 'column': 3, 'body': 3}
    assert calculation['value'] == '5.850' and calculation['unit'] == 'mm/s'


def test_xlsx_presentation_has_typed_operands_number_formats_and_formula_exclusion(agent):
    answer = succeeded(present(agent, 'Calculate the average temperature in worksheet Sheet1.',
                               [('equipment.xlsx', workbook(values=(70, '=B2*2', 80)))]))
    result, = answer['findings']
    assert result['kind'] == 'calculation' and result['value'] == '75.00' and result['unit'] == '\u00b0C'
    assert result['excluded'] == {'missing': 0, 'nonnumeric': 0, 'formulas': 1}
    assert [(c['location']['cell'], c['value_type'], c['value'], c['number_format']) for c in result['evidence']] == [
        ('B1', 'text', 'Temperature (\u00b0C)', 'General'), ('B2', 'number', '70', 'General'), ('B4', 'number', '80', 'General'),
    ]
    assert all(c['location']['sheet'] == 'Sheet1' for c in result['evidence']) and '=B2*2' not in json.dumps(answer)


def test_cross_source_projection_binds_operand_positions_even_with_duplicate_filenames(agent):
    first = table_document(['Equipment', 'Length (m)'], [('Pump A', '0.4')])
    second = table_document(['Equipment', 'Length (cm)'], [('Pump A', '45')])
    answer = succeeded(present(agent, 'Compare Pump A across source 1 and source 2.', [('same.docx', first), ('same.docx', second)]))
    assert [entry['filename'] for entry in answer['sources']] == ['same.docx', 'same.docx']
    finding, = answer['findings']
    row, = finding['rows']
    assert row['difference'] == '0.05' and finding['unit'] == 'm'
    operands = [finding['evidence'][i] for i in row['operands']]
    assert [entry['source_id'] for entry in operands] == [entry['source_id'] for entry in answer['sources']]
    assert [entry['value'] for entry in operands] == ['0.4', '45']


@pytest.mark.parametrize('change', ['source', 'filename', 'coordinate'])
def test_projection_refuses_invalid_original_metadata_even_from_issued_citations(agent, monkeypatch, change):
    original = document_reasoning._DocumentRequest.find_passages

    def invalid(scope, *args):
        citation, = original(scope, *args)
        fields = {'source': {'source_id': 'unknown'}, 'filename': {'filename': 'swapped.pdf'},
                  'coordinate': {'location': replace(citation.location, page=None, paragraph=1)}}[change]
        altered = replace(citation, **fields)
        scope._publish((altered,))  # Simulate a bad producer and exercise projection checks separately.
        return (altered,)

    monkeypatch.setattr(document_reasoning._DocumentRequest, 'find_passages', invalid)
    terminal = failed(present(agent, 'Find the section about pump vibration.', [('inspection.pdf', sample_pdf())]),
                      'CITATION_UNVERIFIED')
    assert terminal['partial'] is None


def test_projected_failure_contains_only_prior_authorized_evidence_and_remains_ephemeral(agent, tmp_path):
    before = {file.name: file.read_bytes() for file in tmp_path.iterdir() if file.is_file()}
    files = [('inspection.pdf', sample_pdf()), ('equipment.xlsx', workbook())]
    result = failed(present(agent, 'Find the section about pump vibration in source 1, then calculate the average temperature in worksheet Missing.',
                            files), 'WORKSHEET_UNAVAILABLE')
    assert [item['filename'] for item in result['partial']['sources']] == ['inspection.pdf']
    assert result['step_id'] and result['operation'] == 'Calculate from the selected table'
    assert before == {file.name: file.read_bytes() for file in tmp_path.iterdir() if file.is_file()}


@pytest.mark.parametrize(('loss', 'code'), [('cancel', 'CANCELLED'), ('revoke', 'AUTHORIZATION_UNAVAILABLE'), ('timeout', 'TIMEOUT')])
def test_loss_after_verified_steps_never_publishes_projected_evidence(agent, monkeypatch, loss, code):
    _, user = agent
    cancelled = False
    now = 0
    monkeypatch.setattr(document_reasoning, 'monotonic', lambda: now)

    async def exercise():
        nonlocal now, cancelled
        files = [('inspection.pdf', sample_pdf())]
        async with aclosing(stream(agent, 'Find the section about pump vibration.', files, present=True,
                                   cancelled=lambda: cancelled)) as workflow:
            events = []
            async for event in workflow:
                events.append(event)
                if event.get('status') == 'completed' and event['description'] == 'Explain the verified findings':
                    if loss == 'cancel':
                        cancelled = True
                    elif loss == 'revoke':
                        user.is_active = False
                    else:
                        now = 16
        return events

    events = run_offline(exercise())
    assert events[-1]['error_code'] == code and events[-1]['partial'] is None and 'answer' not in events[-1]
    assert 'inspection.pdf' not in json.dumps(events)


def test_readiness_distinguishes_offline_verification_from_protected_routes():
    status = report(ready=True, probe_allowed=True)
    assert status['document_intelligence'] == {
        'formats': ['PDF', 'DOCX', 'XLSX'], 'extraction': 'offline_verified', 'reasoning': 'offline_verified',
        'planning': 'offline_verified', 'direct_chat': 'blocked', 'artifact_publication': 'blocked',
        'external_browser': 'unverified',
    }
    assert all(status['capabilities'][key]['state'] == 'blocked' for key in ('document_rag', 'artifact_access'))
