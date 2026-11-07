from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import patch

import pytest
from backend.agent.memory import ConversationMemory


def test_restart_legacy_format_and_concurrent_writes(tmp_path):
    path = tmp_path / 'memory.json'
    legacy = {'old': [{'role': 'user', 'content': 'preserved', 'images': []}]}
    path.write_text(json.dumps(legacy), encoding='utf-8')
    memory = ConversationMemory(storage_file=path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: memory.add_user_message('old', str(n)), range(40)))
    recovered = ConversationMemory(storage_file=path)
    messages = recovered.get_history('old')
    assert messages[0].content == 'preserved'
    assert {m.content for m in messages[1:]} == {str(n) for n in range(40)}


def test_failed_replace_preserves_disk_file(tmp_path):
    path = tmp_path / 'memory.json'
    memory = ConversationMemory(storage_file=path)
    memory.add_user_message('a', 'original')
    prior = path.read_bytes()
    with patch('backend.agent.memory.os.replace', side_effect=OSError('injected failure')):
        with pytest.raises(OSError):
            memory.add_user_message('a', 'second')
    assert path.read_bytes() == prior
    assert not list(tmp_path.glob('.memory-*.tmp'))


def test_corrupt_json_fails_without_overwrite(tmp_path):
    path = tmp_path / 'memory.json'
    path.write_text('{invalid', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        ConversationMemory(storage_file=path)
    assert path.read_text(encoding='utf-8') == '{invalid'
