"""Bounded conversational edits of existing, owned local drafts."""

import asyncio
import hashlib
import json
import re

from backend.rag.local_knowledge import KnowledgeConflict, KnowledgeQuestionError, KnowledgeReceipt, question_parts
from backend.tools.local_drafts import (
    COLUMNS, DRAFT_PRODUCER, DRAFT_TOOL, HEADINGS, DraftConflict, DraftLayout, DraftOperation, DraftRefinement,
    DraftScope, DraftTarget, _json, _source_findings, check_evidence, draft_origin, evidence_messages,
    resolve_evidence, revised_draft, revision_linked,
)


_SECTION = r'(executive summary|summary|source references|references|conclusion|recommendations|visual observations)'
_FINDING = r'(?:(?:finding|row) (\d{1,2})|(first|second|third) (?:finding|row))'
_ORDINALS = {'first': 0, 'second': 1, 'third': 2}


def safe_wording(value):
    return not any((ord(c) < 32 and c not in '\t\n\r') or 0xd800 <= ord(c) <= 0xdfff or ord(c) in (0xfffe, 0xffff)
                   for c in value)


def _section(value):
    return next((key for key, label in HEADINGS.items() if label.lower() == value.lower()), value.lower())


def _column(value):
    return next((key for key, label in COLUMNS.items() if value.lower() in (key, label.lower())), None)


def _index(number, ordinal):
    return int(number) - 1 if number else _ORDINALS[ordinal]


def parse_refinement(message):
    if len(message) > 2400:
        return None
    text = re.sub(r'^please\s+', '', message.strip(), flags=re.I)
    bare = text.rstrip(' .!?')
    plain = bare.lower()
    match = re.fullmatch(r'change (?:the )?(?:format|draft|report) to (docx|xlsx|pdf)', plain)
    if match:
        return DraftOperation(kind='format', format=match[1])
    match = re.fullmatch(rf'make (?:it|(?:the )?{_SECTION}) (shorter|longer|more detailed)', plain)
    if match:
        return DraftOperation(kind='shorten' if match[2] == 'shorter' else 'expand', section=_section(match[1] or 'summary'))
    match = re.fullmatch(rf'(shorten|expand|rewrite) (?:the )?{_SECTION}(?: section)?(?::\s*(.+))?', text if ':' in text else bare, re.I)
    if match:
        return DraftOperation(kind=match[1].lower(), section=_section(match[2]), text=match[3])
    if re.fullmatch(r'(?:use|switch to) a formal tone|make (?:it|the summary) more formal|make (?:this|it) a technical report', plain):
        return DraftOperation(kind='tone', section='summary')
    match = re.fullmatch(r'add (?:an? )?(conclusion|recommendations|executive summary|summary)(?: section)?(?::\s*(.+))?',
                         text if ':' in text else bare, re.I)
    if match:
        return DraftOperation(kind='add_section', section=_section(match[1]), text=match[2])
    match = re.fullmatch(rf'(?:rename|change) (?:the )?{_SECTION}(?: heading| section)? to (.+)', text, re.I)
    if match:
        return DraftOperation(kind='rename_section', section=_section(match[1]), text=match[2])
    match = re.fullmatch(rf'(?:move|put) (?:the )?{_SECTION}(?: section)? (first|last)', plain)
    if match:
        return DraftOperation(kind='move_section', section=_section(match[1]), position=match[2])
    match = re.fullmatch(rf'remove (?:the )?{_SECTION}(?: section)?', plain)
    if match:
        return DraftOperation(kind='remove_section', section=_section(match[1]))
    match = re.fullmatch(r'(?:rename|change) (?:the )?(\w[\w ]{0,60}) (?:column|header) to (.+)', text, re.I)
    if match and (column := _column(match[1])):
        return DraftOperation(kind='rename_column', column=column, text=match[2])
    match = re.fullmatch(r'(?:move|put) (?:the )?(\w[\w ]{0,60}) column (first|last)', plain)
    if match and (column := _column(match[1])):
        return DraftOperation(kind='move_column', column=column, position=match[2])
    match = re.fullmatch(rf'(?:change|replace|rewrite) (?:the )?{_FINDING} (?:to|with) (.+)', text, re.I)
    if match:
        return DraftOperation(kind='replace_finding', index=_index(match[1], match[2]), text=match[3])
    match = re.fullmatch(rf'(remove|explain|move) (?:the )?{_FINDING}(?: (first|last))?', plain)
    if match and (match[1] == 'move') == bool(match[4]):
        return DraftOperation(kind=f'{match[1]}_finding', index=_index(match[2], match[3]), position=match[4])
    if plain == 'remove the unsupported statement':
        return DraftOperation(kind='remove_finding')
    match = re.fullmatch(r'add (?:a |the )?(?:source-backed )?finding:\s*(.+)', text, re.I)
    if match:
        return DraftOperation(kind='query_evidence', text=match[1])
    match = re.fullmatch(r'regenerate (?:the )?(?:draft|report|spreadsheet)(?: from (?:the )?selected sources)?:\s*(.+)', text, re.I)
    if match:
        return DraftOperation(kind='regenerate', text=match[1])
    match = re.fullmatch(r'add (?:a )?(?:comparison )?table(?: comparing (?:the )?(.+))?', plain)
    if match or plain in ('put the values into a table', 'format the findings as a table'):
        return DraftOperation(kind='table', text=match[1] if match else None)
    if re.fullmatch(r'(?:include|add|put|show) (?:the )?(?:source )?(?:citations|references)(?: below each finding| under each finding)?|add (?:a )?source-reference column', plain):
        return DraftOperation(kind='inline_references')
    if re.fullmatch(r'(?:move|put) (?:the )?(?:citations|references) (?:into|in) (?:a |the )?references section', plain):
        return DraftOperation(kind='inline_references', position='last')
    if re.fullmatch(r'(?:include|add) (?:the )?(?:visual|image) observations(?: to the report)?', plain):
        return DraftOperation(kind='add_visual')
    if re.fullmatch(r'move (?:the )?(?:inspection image|image|visual) (?:findings|observations) into a separate section', plain):
        return DraftOperation(kind='move_section', section='visual', position='last')
    if re.fullmatch(r'remove (?:the )?image observations', plain):
        return DraftOperation(kind='remove_visual')
    if re.fullmatch(r'(?:include|add) (?:the )?(?:selected evidence|evidence from the other document)', plain):
        return DraftOperation(kind='add_evidence')
    return None


def follows_draft(memory, session, message):
    """Deictic wording can also refer to an ordinary chat answer."""
    if not re.match(r'^\s*(?:please\s+)?(?:make it (?:shorter|longer|more detailed|more formal)|'
                    r'(?:use|switch to) a formal tone)\b', message, re.I):
        return True
    reply = next((row for row in reversed(memory.get_history(session)) if row.role == 'assistant'), None)
    return bool(reply and reply.extra_fields.get('local_draft_id') and memory.verify_local_reply(session, reply))


def admit_refinement(state, session, identity, operation, target, source_ids, evidence):
    drafts = [row for row in state.local_drafts.owned(session, identity)
              if not row.cancelled and draft_origin(state.engine._memory, session, row)]
    if target is None:
        if len(drafts) != 1:
            raise DraftConflict('Choose which draft to refine in the Drafts panel.' if drafts else 'Create a draft before refining it.')
        row, = drafts
        target = DraftTarget(id=row.id, revision=row.revision)
    else:
        row = next((item for item in drafts if item.id == target.id), None)
        if row is None:
            raise PermissionError('Draft access denied')
    if row.revision != target.revision or not revision_linked(state, identity, row):
        raise DraftConflict('Review the current draft revision before refining it.')
    if operation.kind != 'regenerate' and check_evidence(state, session, identity, row):
        raise DraftConflict('Draft evidence changed or was removed. Create a new draft from current evidence.')
    if operation.kind not in ('add_evidence', 'add_visual') and evidence is not None:
        raise DraftConflict('Use the evidence selection with an Add evidence or Add visual observations request.')
    if operation.kind not in ('table', 'query_evidence', 'regenerate') and source_ids is not None:
        raise DraftConflict('Choose local sources for an Add finding or comparison table request.')
    if operation.kind in ('add_evidence', 'add_visual'):
        if not evidence and operation.kind == 'add_visual':
            visual_turns = [turn.id for turn, _, _, visual in evidence_messages(state.engine._memory, session) if visual]
            evidence = visual_turns if len(visual_turns) == 1 else None
        if not evidence or len(set(evidence)) != len(evidence):
            raise DraftConflict('Select the evidence turns to include in the Drafts panel.')
    sources = None
    if source_ids is not None or operation.kind in ('query_evidence', 'regenerate'):
        if source_ids is None:
            raise DraftConflict('Select the authorized local sources for this finding.')
        try:
            sources = state.local_knowledge.bind_scope(identity[0], source_ids)
        except KeyError:
            raise DraftConflict('The selected local sources are unavailable.') from None
    plan = DraftRefinement(target=target, operation=operation, sources=sources)
    return DraftScope(format=row.format, selection=evidence, evidence=evidence or [], refinement=plan)


def _counts(findings, style):
    attributed = sum(row.kind in ('source_value', 'source_excerpt', 'comparison') for row in findings)
    visual = sum(row.kind == 'visual_interpretation' for row in findings)
    gaps = sum(row.coverage in ('absent', 'incomplete', 'conflict') or row.kind == 'user_edited' for row in findings)
    if style == 'tone':
        return f'The report records {attributed} evidence findings and {visual} visual observations. Review {gaps} qualified findings.'
    if style == 'shorten':
        return f'{attributed} evidence findings, {visual} visual observations, and {gaps} findings requiring review.'
    if style == 'expand':
        return (f'The report contains {attributed} evidence findings and {visual} visual observations. '
                f'Review the {gaps} qualified findings for missing evidence, conflicts, or edited wording. '
                'Use the source references to review the original values, units, and locations.')
    return f'Review {attributed} evidence findings and {visual} visual observations. {gaps} findings need attention before use.'


def _move(items, index, position):
    item = items.pop(index)
    items.insert(0 if position == 'first' else len(items), item)


def _selected_index(operation, findings):
    index = operation.index
    if index is None:
        matches = [i for i, row in enumerate(findings) if row.kind in ('missing', 'user_edited')]
        if len(matches) != 1:
            raise DraftConflict('Specify the number of the finding to remove.')
        index, = matches
    if index >= len(findings):
        raise DraftConflict('Specify an existing finding number.')
    return index


def _revise_presentation(operation, draft, findings, layout, changes):
    kind, section, column, text = operation.kind, operation.section, operation.column, operation.text
    if text and not safe_wording(text):
        raise DraftConflict('Draft wording contains unsupported characters.')
    if section is not None:
        if kind == 'add_section' or (section == 'visual' and kind == 'move_section'):
            if section not in layout.order:
                layout.order.append(section)
        elif section not in layout.order:
            raise DraftConflict('That section is not in the draft. Add it first.')
        if kind == 'rename_section':
            layout.headings[section] = text
        elif kind == 'move_section':
            _move(layout.order, layout.order.index(section), operation.position)
        elif kind == 'remove_section':
            if section == 'references':
                raise DraftConflict('Source references are required. You can show them alongside each finding.')
            layout.order.remove(section)
        else:
            wording = text or _counts(findings, kind)
            if kind == 'shorten':
                current = draft.summary if section == 'summary' else layout.notes.get(section, '')
                if len(wording) >= len(current):
                    wording = 'See the cited findings.'
                if len(wording) >= len(current):
                    raise DraftConflict('This section is already concise.')
            if section == 'summary':
                changes['summary'] = wording
            elif section in ('conclusion', 'recommendations'):
                layout.notes[section] = wording
            else:
                raise DraftConflict('Edit summary, conclusion or recommendations wording. Evidence sections use source content.')
        return [section]
    if kind == 'format':
        changes['format'] = operation.format
    elif kind == 'rename_column':
        layout.headers[column] = text
    elif kind == 'move_column':
        _move(layout.columns, layout.columns.index(column), operation.position)
    elif kind == 'inline_references':
        layout.inline_references = operation.position != 'last'
        return ['references']
    elif kind == 'remove_visual':
        findings[:] = [row for row in findings if not row.image]
        if 'visual' in layout.order:
            layout.order.remove('visual')
        return ['visual']
    elif kind.endswith('_finding'):
        index = _selected_index(operation, findings)
        if kind == 'move_finding':
            _move(findings, index, operation.position)
        elif kind == 'remove_finding':
            findings.pop(index)
        elif kind == 'replace_finding':
            findings[index] = findings[index].model_copy(update={'text': text, 'kind': 'user_edited'})
        else:
            row = findings[index]
            origin = 'the original source reference' if row.source else 'the finding and its evidence status'
            layout.notes['conclusion'] = (f'Finding {index + 1} is recorded as {row.kind.replace("_", " ")}. '
                                          f'Review {origin} alongside the reported wording.')
            if 'conclusion' not in layout.order:
                layout.order.append('conclusion')
            return ['conclusion']
    return []


def prepare_revision(state, identity, draft, scope, turn_id, attempt, prompt):
    plan = scope.refinement
    operation = plan.operation
    findings = list(draft.findings)
    layout = draft.layout.model_copy(deep=True) if draft.layout else DraftLayout()
    changes = {}
    if scope.evidence:
        resolved = resolve_evidence(state, draft.session, identity, scope.evidence)
        if len(resolved) != len(scope.evidence):
            raise DraftConflict('The selected evidence is missing, stale, or outside this conversation.')
        additions = list(draft.added_evidence or [])
        known = {ref.turn_id for ref in additions}
        if not draft.baseline_replaced:
            known.update(ref.turn_id for ref in draft.evidence)
        for key in scope.evidence:
            ref, rows = resolved[key]
            visual = [row for row in rows if row.kind == 'visual_interpretation']
            if operation.kind == 'add_visual' and not visual:
                raise DraftConflict('Choose a completed image analysis to include.')
            if key not in known:
                additions.append(ref)
            findings.extend(row for row in (visual if operation.kind == 'add_visual' else rows) if row not in findings)
        changes['added_evidence'] = additions or None
    if plan.sources:
        corpus = state.local_knowledge
        query = operation.text or 'Compare the selected findings'
        if operation.kind == 'table' and operation.text:
            query = 'Compare ' + operation.text
        parts = question_parts(query)
        contexts = corpus.contexts(identity[0], parts, plan.sources)
        _, raw = corpus.answer(identity[0], plan.sources, [(part, refs, json.dumps({'passages': list(refs)}))
                                                          for part, refs in zip(parts, contexts)])
        receipt = KnowledgeReceipt.model_validate({**raw, 'turn_id': turn_id, 'attempt': attempt,
                                                  'request_digest': hashlib.sha256(prompt.encode('utf-8')).hexdigest()})
        public = corpus.public_receipt(identity[0], receipt)
        if public is None:
            raise KnowledgeConflict('The selected evidence changed.')
        rows = _source_findings(turn_id, public)
        if not any(row.source for row in rows):
            raise DraftConflict('The selected sources do not support that finding. The draft was not changed.')
        if operation.kind == 'regenerate':
            findings = rows
            layout = DraftLayout()
            changes.update(regenerated=[receipt], baseline_replaced=True, added_evidence=None,
                           layout=None, summary=_counts(rows, 'rewrite'))
        else:
            changes['regenerated'] = [*(draft.regenerated or []), receipt]
            findings.extend(rows)
    sections = _revise_presentation(operation, draft, findings, layout, changes)
    if operation.kind == 'regenerate':
        sections = ['summary', 'references']
    if operation.kind == 'table':
        attribute = 'temperature' if 'temperatur' in (operation.text or '') else None
        if operation.text and not attribute and not plan.sources:
            raise DraftConflict('Select authorized sources for the requested comparison, or ask for a table of the existing values.')
        measured = [row for row in findings if row.kind == 'source_value' and
                    (not attribute or attribute in (row.field or {}).get('attribute', ''))]
        if len(measured) < 2:
            raise DraftConflict('At least two source-verified values are required for the comparison table.')
        layout.comparison, layout.comparison_attribute = True, attribute
    if not findings:
        raise DraftConflict('A grounded draft must retain at least one finding.')
    if layout != (draft.layout or DraftLayout()):
        changes['layout'] = DraftLayout.model_validate(layout.model_dump())
    if findings != draft.findings:
        changes['findings'] = findings
    if not any(getattr(draft, key) != value for key, value in changes.items()):
        raise DraftConflict('That request makes no change to the current draft.')
    return revised_draft(draft, operation, plan=plan, turn_id=turn_id, sections=sections, **changes)


async def finish_refinement(engine, run, prompt):
    from backend.agent.engine import LocalToolError

    user = run.check_workflow_active()
    if not run.tools_enabled or not engine._tool_registry.allows_local_tool(DRAFT_TOOL, user.id, user.role):
        raise LocalToolError('LOCAL_TOOL_UNAUTHORIZED')
    state = run.app_state
    plan = run.draft_scope.refinement
    store = state.local_drafts
    current = store.records.get(plan.target.id)
    if (current is None or (current.session, current.generation) != (run.session_id, run.identity[1])
            or not draft_origin(engine._memory, run.session_id, current)):
        raise LocalToolError('LOCAL_DRAFT_TARGET_INVALID')
    run.emit('agent_status', 'drafting')
    try:
        replacing = plan.operation.kind == 'regenerate' and current.revision == plan.target.revision
        if current.cancelled or (not replacing and check_evidence(state, run.session_id, run.identity, current)):
            raise LocalToolError('LOCAL_KNOWLEDGE_CHANGED')
        if (current.change and current.change.turn_id == run.turn_id and current.change.plan_digest == plan.digest()
                and current.change.parent == plan.target.revision):
            updated = current  # Recover a saved revision whose chat acknowledgement was interrupted.
        elif current.revision == plan.target.revision and revision_linked(state, run.identity, current):
            updated = prepare_revision(state, run.identity, current, run.draft_scope, run.turn_id, run.attempt, prompt)
        else:
            raise LocalToolError('LOCAL_DRAFT_STALE_REVISION')
        await asyncio.sleep(0)
        run.check_workflow_active()
        if store.records.get(current.id) != current:
            raise LocalToolError('LOCAL_DRAFT_STALE_REVISION')
        if check_evidence(state, run.session_id, run.identity, updated):
            raise LocalToolError('LOCAL_KNOWLEDGE_CHANGED')
        if updated != current:
            store.save(updated)
        engine._memory.record_plain_response(
            run.session_id, run.identity, f'Revision {updated.revision} is ready in Drafts. Review it and approve that revision before export.',
            producer=DRAFT_PRODUCER, local_turn=(run.turn_id, run.attempt), local_draft_id=updated.id,
        )
    except KnowledgeConflict:
        raise LocalToolError('LOCAL_KNOWLEDGE_CHANGED') from None
    except KnowledgeQuestionError:
        raise LocalToolError('LOCAL_KNOWLEDGE_QUESTION') from None
    except DraftConflict as exc:
        code = 'LOCAL_DRAFT_UNSUPPORTED_EVIDENCE' if 'do not support' in str(exc) else 'LOCAL_DRAFT_EDIT_INVALID'
        raise LocalToolError(code) from None
    except ValueError:
        raise LocalToolError('LOCAL_DRAFT_EDIT_INVALID') from None
