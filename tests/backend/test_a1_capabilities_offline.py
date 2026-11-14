"""Pure capability derivation checks; no application startup or network."""
import pytest

from backend.health.capabilities import ModelObservation, capability_report, loopback_tags_url, parse_tags


def report(observation=None, ready=True, probe_allowed=True):
    return capability_report(environment='development', ready=ready, planning=True,
        persistence=True, configured_models={'default_chat': 'ollama/qwen:7b',
        'image_chat': 'ollama/qwen:7b', 'vision': 'ollama/vision:7b',
        'embedding': 'ollama/embed:latest'}, probe_allowed=probe_allowed, observation=observation)


def test_passive_unknown_and_security_gates():
    caps = report()['capabilities']
    assert caps['ollama']['state'] == 'unknown'
    assert all(caps[name]['state'] == 'blocked' for name in
               ('document_rag', 'approval_resume', 'artifact_access', 'code_execution'))


def test_unavailable_and_recovery_do_not_claim_inference():
    assert report(ModelObservation('unavailable', 'ollama_check_failed'))['capabilities']['chat']['state'] == 'unavailable'
    recovered = report(ModelObservation('available', 'inventory_observed', ('qwen:7b', 'vision:7b', 'embed:latest')))
    assert recovered['models']['default_chat']['state'] == 'available'
    assert recovered['capabilities']['chat']['state'] == 'unknown'
    assert report(ready=False)['capabilities']['backend']['state'] == 'unavailable'


@pytest.mark.parametrize('url', ['http://example.com:11434', 'http://127.0.0.1:11434/path', 'https://127.0.0.1', 'http://user@127.0.0.1'])
def test_nonloopback_or_unsafe_endpoints_rejected(url):
    with pytest.raises(ValueError):
        loopback_tags_url(url)


def test_inventory_validation_and_loopback():
    assert loopback_tags_url('http://localhost:11434') == 'http://127.0.0.1:11434/api/tags'
    assert parse_tags({'models': [{'name': 'qwen:7b'}]}) == ('qwen:7b',)
    with pytest.raises(ValueError):
        parse_tags({'models': [{'name': '../bad name'}]})


def test_model_missing_and_invalid_endpoint_fail_closed():
    observed = ModelObservation('available', 'inventory_observed', ('other:latest',))
    assert report(observed)['capabilities']['chat']['state'] == 'unavailable'
    assert report(observed, probe_allowed=False)['capabilities']['ollama']['state'] == 'blocked'
