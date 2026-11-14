from types import SimpleNamespace

import pytest

from tools.offline_verification.chroma_probe import expected, require_persistent, validate


def test_nonpersistent_fallback_is_rejected():
    class Fallback:
        pass
    for store in (SimpleNamespace(_client=None, _collection=object()),
                  SimpleNamespace(_client=object(), _collection=Fallback())):
        with pytest.raises(RuntimeError):
            require_persistent(store, Fallback)


def test_manifest_comparison_rejects_missing_extra_and_changed():
    rows = expected()
    ids = list(rows)
    valid = {'ids': ids, 'documents': [rows[k][0] for k in ids],
             'metadatas': [rows[k][1] for k in ids]}
    validate(valid)
    for bad in ({'ids': ids[:1], 'documents': valid['documents'][:1], 'metadatas': valid['metadatas'][:1]},
                {'ids': ids + ['extra'], 'documents': valid['documents'] + ['extra'], 'metadatas': valid['metadatas'] + [{}]},
                {'ids': ids, 'documents': ['modified', valid['documents'][1]], 'metadatas': valid['metadatas']}):
        with pytest.raises(RuntimeError):
            validate(bad)
