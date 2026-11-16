"""Bound selected multipart bodies before Starlette's multipart parser."""
import tempfile
from backend.multimodal.image_processor import DEFAULT_MAX_SIZE_BYTES
from backend.multimodal.text_file import MAX_TEXT_FORM_BYTES

_TEXT_FILE_PATHS = ('/api/chat/text-preview', '/api/chat/text-file', '/api/chat/csv-preview', '/api/chat/csv-file')


class MultipartBodyLimitMiddleware:
    def __init__(self, app, max_file_bytes: int, multipart_overhead: int = 1024 * 1024):
        self.app = app
        self.limit = max_file_bytes + multipart_overhead
        self.image_limit = min(max_file_bytes, DEFAULT_MAX_SIZE_BYTES) + multipart_overhead

    async def __call__(self, scope, receive, send):
        path = scope.get('path')
        if (scope['type'] != 'http' or scope.get('method') != 'POST'
                or path not in ('/api/documents', '/api/chat/multimodal', *_TEXT_FILE_PATHS)):
            return await self.app(scope, receive, send)

        headers = dict(scope.get('headers', []))
        if path == '/api/documents' and b'multipart/form-data' not in headers.get(b'content-type', b'').lower():
            return await self.app(scope, receive, send)
        if path in _TEXT_FILE_PATHS:
            limit = MAX_TEXT_FORM_BYTES
        else:
            limit = self.image_limit if path == '/api/chat/multimodal' else self.limit

        async def reject():
            await send({'type': 'http.response.start', 'status': 413,
                        'headers': [(b'content-type', b'text/plain')]})
            await send({'type': 'http.response.body', 'body': b'Request body too large.'})

        try:
            content_length = int(headers.get(b'content-length', b'0'))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > limit:
            return await reject()

        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode='w+b') as body:
            total = 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                total += len(chunk)
                if total > limit:
                    return await reject()
                body.write(chunk)
                if not message.get('more_body', False):
                    break
            body.seek(0)
            remaining = total
            sent_empty = False

            async def bounded_receive():
                nonlocal remaining, sent_empty
                if remaining:
                    chunk = body.read(min(64 * 1024, remaining))
                    remaining -= len(chunk)
                    return {'type': 'http.request', 'body': chunk, 'more_body': remaining > 0}
                if total == 0 and not sent_empty:
                    sent_empty = True
                    return {'type': 'http.request', 'body': b'', 'more_body': False}
                return await receive()

            await self.app(scope, bounded_receive, send)
