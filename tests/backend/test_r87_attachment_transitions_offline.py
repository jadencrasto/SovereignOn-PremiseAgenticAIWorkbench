"""Exercise mixed attachment turns through the production chat routes offline."""

import pytest

from backend.models.base import ChatResponse
from test_a22_async_offline import run_offline
from test_a61_multimodal_offline import image_form
from test_r83_text_file_offline import Provider, app_with_text_profile, ask_form, events, text_upload
from test_r86_csv_preview_offline import client, upload as csv_upload


def attachment_request(kind, question):
    if kind == 'image':
        return '/api/chat/multimodal', image_form(message=question)
    if kind == 'txt':
        return '/api/chat/text-file', {'data': ask_form(message=question), 'files': text_upload()}
    return '/api/chat/csv-file', {
        'data': ask_form(message=question, row_start='1', row_end='1'), 'files': csv_upload(),
    }


@pytest.mark.parametrize('previous,current,question,expected_error', [
    ('csv', 'image', 'What is in the CSV?', 'TEXT_FILE_REATTACH_REQUIRED'),
    ('csv', 'txt', 'Summarize the CSV.', 'TEXT_FILE_REATTACH_REQUIRED'),
    ('txt', 'csv', 'Read the text file.', 'TEXT_FILE_REATTACH_REQUIRED'),
    ('image', 'csv', 'Describe the screenshot.', 'IMAGE_REATTACH_REQUIRED'),
    ('image', 'txt', 'What color is it?', None),
    ('csv', 'image', 'What is in the table?', None),
    ('csv', 'csv', 'What is in the CSV?', None),
])
def test_current_attachment_does_not_substitute_for_an_explicit_other_format(tmp_path, previous, current,
                                                                                question, expected_error):
    class MixedProvider(Provider):
        async def chat(self, request):
            self.requests.append(request)
            return ChatResponse('A blue square.', request.model, self.provider_name)

    provider = MixedProvider()
    app = app_with_text_profile(tmp_path, provider)

    async def exercise():
        async with client(app) as http:
            path, form = attachment_request(previous, 'Describe the attached data.')
            first = await http.post(path, **form)
            assert first.status_code == 200 and events(first)[-1]['type'] == 'done'
            history = '/api/chat/sessions/session-a'
            before = (await http.get(history)).json()['messages']
            request_count = len(provider.requests)

            path, form = attachment_request(current, question)
            second = await http.post(path, **form)
            if expected_error:
                assert second.status_code == 409
                assert second.headers['X-Error-Code'] == expected_error
                assert len(provider.requests) == request_count
                assert (await http.get(history)).json()['messages'] == before
            else:
                assert second.status_code == 200 and events(second)[-1]['type'] == 'done'
                assert len(provider.requests) > request_count
                for request in provider.requests[request_count:]:
                    assert all('Describe the attached data.' not in message.content for message in request.messages)
                    if current != 'image':
                        assert all(not message.images for message in request.messages)

    run_offline(exercise())
