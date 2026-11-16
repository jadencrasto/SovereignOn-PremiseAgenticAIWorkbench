"""Unverified cross-turn numeric references must not reach a provider."""
import json
import httpx
import pytest
from backend.agent.numeric_receipts import requires_prior_numeric_receipt
from test_a21_conversation_offline import setup_engine, make_app
from test_a22_async_offline import run_offline


@pytest.mark.parametrize('message', [
    'Multiply that result by 3.', 'Multiply it by 3.',
    'Convert that number from kilometres to metres.',
    'Use the result from before and calculate 3 times that value.',
    'Calculate the previous answer plus 2.',
    'multiply that by 3', 'Add 5', 'Please add -2.5.',
    'Multiply that by three',
])
def test_explicit_unverified_followup_is_recognized(message):
    assert requires_prior_numeric_receipt(message)


def test_normal_questions_are_not_treated_as_result_references():
    assert not requires_prior_numeric_receipt('What is a pump?')
    assert not requires_prior_numeric_receipt('Calculate 25 + 17')
    assert not requires_prior_numeric_receipt('Calculate 25 + 17, then multiply that result by 3.')
    assert not requires_prior_numeric_receipt('Tell me more about that result')
    assert not requires_prior_numeric_receipt('Add 5 items to the list')


@pytest.mark.parametrize('message', ['Multiply that result by 3.', 'multiply that by 3', 'add 5'])
def test_actual_chat_route_denies_reference_without_model_or_cross_owner_leak(tmp_path, message):
    engine, captured = setup_engine(tmp_path); app = make_app(engine)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            body={'session_id':'s','message':message,'stream':True,'tools_enabled':True}
            response=await client.post('/api/chat',headers={'Authorization':'Bearer a'},json=body)
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert response.status_code==200 and len(events)==1
            assert events[0]['type']=='error' and events[0]['error_code']=='RESULT_REFERENCE_UNAVAILABLE'
            assert 'include the number' in events[0]['content']
            assert (await client.post('/api/chat',headers={'Authorization':'Bearer b'},json=body)).status_code==404
            body['stream']=False
            assert (await client.post('/api/chat',headers={'Authorization':'Bearer a'},json=body)).status_code==409
    run_offline(run())
    assert captured == []
