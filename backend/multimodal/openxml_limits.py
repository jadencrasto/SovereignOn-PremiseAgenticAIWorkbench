"""Preflight bounded Office ZIP parts before opening them with existing readers."""

from io import BytesIO
from posixpath import dirname, join, normpath
from xml.parsers import expat
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from backend.multimodal.document_evidence import ExtractionBudget

_MAX_ENTRIES = 10_000
_MAX_PART_BYTES = 8 * 1024 * 1024
_MAX_XML_ELEMENTS = 250_000
_MAX_XML_DEPTH = 64
_REL = 'http://schemas.openxmlformats.org/package/2006/relationships}Relationship'
_CONTENT_TYPE_PREFIX = 'http://schemas.openxmlformats.org/package/2006/content-types}'


def validate_openxml(data: bytes, main_part: str, max_expanded: int, budget: ExtractionBudget, *, sheet_limit=None) -> None:
    with ZipFile(BytesIO(data)) as archive:
        entries = archive.infolist()
        names = {part.filename for part in entries}
        normalized = {name.casefold() for name in names}
        if (len(entries) > _MAX_ENTRIES or len(normalized) != len(entries)
                or sum(part.file_size for part in entries) > max_expanded
                or not {main_part, '[Content_Types].xml', '_rels/.rels'} <= names
                or any(name.endswith('vbaproject.bin') or name.startswith((
                    'xl/externallinks/', 'xl/macrosheets/', 'xl/intlmacrosheets/',
                    'word/embeddings/', 'xl/embeddings/', 'word/activex/', 'xl/activex/',
                )) for name in normalized)):
            raise ValueError('Unsupported or oversized Office archive.')
        element_count = 0
        for part in entries:
            budget.check()
            name = part.filename
            if (part.file_size > _MAX_PART_BYTES or part.compress_type not in (ZIP_DEFLATED, ZIP_STORED)
                    or part.flag_bits & 1 or name.startswith('/') or '\\' in name or ':' in name
                    or any(segment in ('', '.', '..') for segment in name.rstrip('/').split('/'))):
                raise ValueError('Unsupported or oversized Office archive member.')
            # Validate every XML part, including relationship and content-type metadata.
            if name.lower().endswith(('.xml', '.rels')):
                element_count = _validate_xml(archive, part, names, element_count, budget, main_part, sheet_limit)


def _validate_xml(archive, part, names, element_count, budget, main_part, sheet_limit):
    parser = expat.ParserCreate(namespace_separator='}')
    depth = 0
    ids = set()
    sheet_names = set()
    main_relationship = False

    def forbidden(*_args):
        raise ValueError('Document type declarations or external entities are unavailable.')

    def start(tag, attributes):
        nonlocal depth, element_count, main_relationship
        depth += 1
        element_count += 1
        if depth > _MAX_XML_DEPTH or element_count > _MAX_XML_ELEMENTS:
            raise ValueError('Office XML exceeds the parser limits.')
        if tag == _REL:
            target = attributes.get('Target', '')
            mode = attributes.get('TargetMode', 'Internal')
            rel_id = attributes.get('Id')
            rel_type = attributes.get('Type', '').lower().rsplit('/', 1)[-1]
            base = dirname(dirname(part.filename))
            location = normpath(target.lstrip('/') if target.startswith('/') else join(base, target))
            if (mode != 'Internal' or not target or target.startswith('//') or any(char in target for char in '\\:#?%')
                    or location not in names or not rel_id or rel_id in ids or not rel_type
                    or rel_type in ('oleobject', 'package', 'vbaproject', 'externallink', 'attachedtemplate')):
                raise ValueError('Unsupported Office relationship.')
            if part.filename == '_rels/.rels' and rel_type == 'officedocument':
                if main_relationship or location != main_part:
                    raise ValueError('Office document root relationship is ambiguous.')
                main_relationship = True
            ids.add(rel_id)
        elif tag.startswith(_CONTENT_TYPE_PREFIX):
            content_type = attributes.get('ContentType', '').casefold()
            if any(mark in content_type for mark in ('macroenabled', 'vbaproject', 'oleobject', 'activex')):
                raise ValueError('Unsupported active Office content.')
        elif part.filename == 'xl/workbook.xml' and tag == 'http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet':
            name = attributes.get('name', '').casefold()
            if not name or name in sheet_names:
                raise ValueError('Worksheet name is missing or ambiguous.')
            sheet_names.add(name)
            if sheet_limit is not None and len(sheet_names) > sheet_limit:
                raise ValueError('Workbook sheet limit exceeded.')

    def end(_tag):
        nonlocal depth
        depth -= 1

    parser.StartDoctypeDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    with archive.open(part) as stream:
        while chunk := stream.read(64 * 1024):
            budget.check()
            parser.Parse(chunk, False)
        parser.Parse(b'', True)
    if part.filename == '_rels/.rels' and not main_relationship:
        raise ValueError('Office document root relationship is missing.')
    return element_count
