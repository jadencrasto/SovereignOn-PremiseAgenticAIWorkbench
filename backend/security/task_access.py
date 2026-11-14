"""Fail-closed policy for task records without transitive provenance.

No current task schema records enough source lineage for clearance checks.
This is deliberately not configurable through user input or environment.
"""
TASK_CONTENT_WITHHELD = 'Task content unavailable: source authorization cannot be verified.'
TASK_RESUME_WITHHELD = 'Task continuation unavailable: source authorization cannot be verified.'

# Version 1 is a quarantine envelope, not a clearance grant. Dependencies
# and classification remain unknown until a future trusted capture pipeline.
TASK_PROVENANCE_SCHEMA_VERSION = 1
TASK_PROVENANCE_UNVERIFIED = 'unverified'


def task_content_revalidation_available() -> bool:
    return False
