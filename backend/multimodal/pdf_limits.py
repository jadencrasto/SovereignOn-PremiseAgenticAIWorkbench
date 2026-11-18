"""Preflight the bounded PDF profile used by the existing text/table readers."""

import io
import re
import zlib

from backend.multimodal.document_evidence import ExtractionLimit

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 100
_MAX_OBJECTS = 4096
_MAX_STREAM_BYTES = 2 * 1024 * 1024
_MAX_DECODED_BYTES = 16 * 1024 * 1024


def open_bounded_pdf(content, budget):
    """Reject unsupported structure before text, table or image interpretation."""
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, IndirectObject, NullObject, StreamObject

    class BoundedReader(PdfReader):
        def _read_pdf15_xref_stream(self, *_args):
            raise ValueError('PDF cross-reference streams are outside the bounded profile.')

        def _rebuild_xref_table(self, *_args):
            raise ValueError('PDF cross-reference recovery is unavailable.')

        def _read_xref(self, stream):
            budget.check()
            if super()._read_xref(stream) is not None:
                raise ValueError('Incremental PDFs are outside the bounded profile.')
            return None

    if len(content) > MAX_PDF_BYTES:
        raise ExtractionLimit('PDF exceeds the parser size limit.')
    footer = re.search(rb'startxref\s+([0-9]{1,12})\s+%%EOF\s*\Z', content[-1024:])
    if not footer:
        raise ValueError('PDF has no bounded cross-reference table.')
    offset = int(footer[1])
    if content[offset:offset + 4] != b'xref' or not callable(getattr(PdfReader, '_read_xref', None)):
        raise ValueError('PDF cross-reference format is outside the bounded profile.')
    byte_stream = io.BytesIO(content)
    try:
        reader = BoundedReader(byte_stream, strict=True)
        if reader.is_encrypted or reader.xref_objStm:
            raise ValueError('Encrypted or compressed-object PDFs are unavailable.')
        if sum(map(len, reader.xref.values())) > _MAX_OBJECTS:
            raise ExtractionLimit('PDF exceeds the object limit.')
        decoded = {}
        total = 0
        for generation, offsets in reader.xref.items():
            for number in offsets:
                budget.check()
                reference = IndirectObject(number, generation, reader)
                obj = reader.get_object(reference)
                if hasattr(obj, 'get') and obj.get('/Subtype') == '/Type3':
                    raise ValueError('PDF programmable glyph streams are unavailable.')
                if not isinstance(obj, StreamObject):
                    continue
                filters = obj.get('/Filter')
                if isinstance(filters, ArrayObject) and len(filters) == 1:
                    filters = filters[0]
                size = _decoded_size(obj, filters, NullObject)
                total += size
                if total > _MAX_DECODED_BYTES:
                    raise ExtractionLimit('PDF exceeds the decoded stream budget.')
                decoded[(generation, number)] = size
        count = _validate_pages(reader.trailer['/Root'].raw_get('/Pages'), decoded, ArrayObject, IndirectObject, budget)
        if len(reader.pages) != count:
            raise ValueError('PDF page count could not be verified.')
        return reader
    except BaseException:
        byte_stream.close()
        raise


def _decoded_size(obj, filters, null_type):
    raw = obj._data  # Encoded bytes as read by pypdf, before filter dispatch.
    if len(raw) > _MAX_STREAM_BYTES:
        raise ExtractionLimit('PDF stream exceeds the parser limit.')
    if obj.get('/Type') in ('/ObjStm', '/XRef') or obj.get('/Subtype') == '/Form':
        raise ValueError('Compressed object or nested form streams are unavailable.')
    if filters is None or isinstance(filters, null_type):
        return len(raw)
    if filters == '/DCTDecode' and obj.get('/Subtype') == '/Image':
        return len(raw)  # JPEG remains opaque. Neither text extractor renders images.
    if filters != '/FlateDecode' or '/DecodeParms' in obj:
        raise ValueError('PDF stream encoding is outside the bounded profile.')
    inflater = zlib.decompressobj()
    result = inflater.decompress(raw, _MAX_STREAM_BYTES + 1)
    if len(result) > _MAX_STREAM_BYTES or inflater.unconsumed_tail:
        raise ExtractionLimit('PDF stream exceeds the decoded size limit.')
    if not inflater.eof or inflater.unused_data:
        raise ValueError('Invalid compressed PDF stream.')
    return len(result)


def _validate_pages(root, decoded, array_type, reference_type, budget):
    stack = [(root, 1)]
    seen = set()
    count = 0
    page_bytes = 0
    while stack:
        budget.check()
        reference, depth = stack.pop()
        if not isinstance(reference, reference_type) or depth > 32:
            raise ValueError('PDF page hierarchy is unsupported.')
        key = (reference.generation, reference.idnum)
        if key in seen or len(seen) >= _MAX_OBJECTS:
            raise ValueError('PDF page references are ambiguous or excessive.')
        seen.add(key)
        node = reference.get_object()
        if node.get('/Type') == '/Pages':
            children = node['/Kids']
            if not isinstance(children, array_type) or len(children) + len(stack) > _MAX_OBJECTS:
                raise ExtractionLimit('PDF exceeds the page hierarchy limit.')
            stack.extend((child, depth + 1) for child in children)
        elif node.get('/Type') == '/Page':
            count += 1
            if count > MAX_PDF_PAGES:
                raise ExtractionLimit('PDF exceeds the page limit.')
            streams = node.get('/Contents')
            if isinstance(streams, reference_type):
                streams = streams.get_object() if (streams.generation, streams.idnum) not in decoded else [streams]
            if streams is None:
                continue
            if not isinstance(streams, (array_type, list)) or len(streams) > 128:
                raise ValueError('PDF page contents could not be verified.')
            size = 0
            for entry in streams:
                if not isinstance(entry, reference_type) or (entry.generation, entry.idnum) not in decoded:
                    raise ValueError('PDF page contents could not be verified.')
                size += decoded[(entry.generation, entry.idnum)]
            if size > _MAX_STREAM_BYTES:
                raise ExtractionLimit('PDF page exceeds the decoded content limit.')
            page_bytes += size
            if page_bytes > _MAX_DECODED_BYTES:
                raise ExtractionLimit('PDF pages exceed the decoded content budget.')
        else:
            raise ValueError('PDF page hierarchy is invalid.')
    return count
