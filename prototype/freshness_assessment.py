"""Non-authorizing synthetic freshness comparison; no trusted witness exists.

The caller-supplied observations are not independently verified. Matching
values must never be used as evidence of authorization or rollback resistance.
"""
import re


def assess(db_generation, witness):
    if type(db_generation) is not int or db_generation < 0:
        return 'INVALID'
    if witness is None:
        return 'MISSING'
    if (type(witness) is not dict or set(witness) != {'generation', 'commitment'} or
        type(witness['generation']) is not int or witness['generation'] < 0 or
        not isinstance(witness['commitment'], str) or
        re.fullmatch(r'[0-9a-f]{64}', witness['commitment']) is None):
        return 'INVALID'
    if witness['generation'] != db_generation:
        return 'MISMATCH'
    return 'MATCH_UNVERIFIED'


def authorization_available():
    return False
