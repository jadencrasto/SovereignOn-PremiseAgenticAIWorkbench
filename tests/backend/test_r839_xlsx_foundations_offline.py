"""Exercise the existing byte-only XLSX parser, away from attachment routes."""

import io
import re
import zipfile

import pytest
from openpyxl import Workbook

from backend.multimodal import spreadsheet


def workbook_bytes():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Readings'
    sheet.append(['Pump', 'Pressure', 'Note'])
    sheet.append(['A', 10, None])
    sheet.append(['B', '=10+5', None])
    sheet.append(['C', 18, 'last'])
    sheet.merge_cells('C2:C3')
    workbook.create_sheet('Second').append(['Identifier', 4])
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def rewrite(data, changes=None, extra=None):
    changes = changes or {}
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, 'w') as result:
        for name in source.namelist():
            content = source.read(name)
            result.writestr(name, changes[name](content) if name in changes else content)
        if extra:
            result.writestr(extra, b'')
    return output.getvalue()


def worksheet_dimension(data, dimension):
    return re.sub(rb'<dimension ref="[^"]+"\s*/>', dimension, data)


@pytest.mark.parametrize('dimension', [b'', b'<dimension ref="A1:A1"/>'])
def test_missing_or_stale_dimensions_preserve_worksheet_identity_coordinates_and_formula_policy(dimension):
    data = rewrite(workbook_bytes(), {
        'xl/worksheets/sheet1.xml': lambda content: worksheet_dimension(content, dimension),
    })
    parsed = spreadsheet.parse_spreadsheet('synthetic.xlsx', data)
    assert [sheet.name for sheet in parsed.sheets] == ['Readings', 'Second']
    assert parsed.sheets[0].rows[3] == ('C', 18, 'last')
    assert parsed.sheets[0].rows[2] == ('B', '=10+5', None)
    assert spreadsheet.numeric_summary(parsed.sheets[0], 1) == {
        'count': 2, 'missing': 0, 'nonnumeric': 1, 'mean': 14.0, 'max': 18,
    }


def test_observed_cells_use_budget_even_without_declared_dimensions(monkeypatch):
    data = rewrite(workbook_bytes(), {
        'xl/worksheets/sheet1.xml': lambda content: worksheet_dimension(content, b''),
    })
    monkeypatch.setattr(spreadsheet, 'MAX_CELLS', 11)
    with pytest.raises(ValueError, match='cell limit'):
        spreadsheet.parse_spreadsheet('synthetic.xlsx', data)


def test_formula_with_stale_numeric_cache_is_still_nonnumeric():
    data = rewrite(workbook_bytes(), {
        'xl/worksheets/sheet1.xml': lambda content: content.replace(
            b'<f>10+5</f><v></v>', b'<f>10+5</f><v>999</v>',
        ),
    })
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert b'<f>10+5</f><v>999</v>' in archive.read('xl/worksheets/sheet1.xml')
    sheet = spreadsheet.parse_spreadsheet('synthetic.xlsx', data).sheets[0]
    assert sheet.rows[2][1] == '=10+5'
    assert spreadsheet.numeric_summary(sheet, 1)['max'] == 18


@pytest.mark.parametrize('name', [
    'xl/ExternalLinks/externalLink1.xml', 'xl/vbaProject.bin', 'xl/macrosheets/sheet1.xml', 'XL/WORKBOOK.XML',
])
def test_rejects_unsupported_or_ambiguous_archive_parts(name):
    data = rewrite(workbook_bytes(), extra=name)
    with pytest.raises(ValueError, match='Unsupported or oversized workbook'):
        spreadsheet.parse_spreadsheet('synthetic.xlsx', data)


def test_rejects_expanded_workbook_over_configured_budget(monkeypatch):
    data = workbook_bytes()
    monkeypatch.setattr(spreadsheet, 'MAX_EXPANDED_BYTES', 1000)
    with pytest.raises(ValueError, match='oversized workbook'):
        spreadsheet.parse_spreadsheet('synthetic.xlsx', data)


def test_empty_and_sparse_sheets_keep_names_and_observed_coordinates():
    workbook = Workbook()
    workbook.active.title = 'Empty'
    sparse = workbook.create_sheet('Sparse')
    sparse['C4'] = 21
    sparse['A6'] = 4
    workbook.properties.description = 'Synthetic metadata outside the cell table.'
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    parsed = spreadsheet.parse_spreadsheet('synthetic.xlsx', stream.getvalue())
    assert parsed.sheets[0] == spreadsheet.Sheet('Empty', ())
    assert parsed.sheets[1].name == 'Sparse'
    assert len(parsed.sheets[1].rows) == 6
    assert parsed.sheets[1].rows[3] == (None, None, 21)
    assert parsed.sheets[1].rows[5] == (4, None, None)
