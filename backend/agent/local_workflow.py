"""Compose the existing local evidence, vision and draft capabilities in one chat turn."""

import asyncio
import hashlib
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from backend.agent.plan_execution import execute_plan_action
from backend.agent.planner import AgentPlan, PlanStep
from backend.auth.request_context import local_chat_tool_turn, local_draft_scope, request_tools_enabled
from backend.rag.local_knowledge import (
    KNOWLEDGE_FIELD, KNOWLEDGE_PRODUCER, MAX_CONTEXT_PASSAGES, MAX_QUESTION_PARTS, KnowledgeConflict, KnowledgeQuestionError,
    KnowledgeReceipt, question_parts,
)
from backend.rag.local_relevance import entities, measurements, requested_fact
from backend.tools.local_drafts import (
    DRAFT_PRODUCER, DRAFT_TOOL, MAX_FINDINGS, Digest, DraftConflict, DraftRequest, Finding, LocalDraft,
    StrictModel, _json, _source_findings, artifact_clauses, draft_reply,
)
from backend.tools.unit_conversion import LocalUnitConversionOutput, exact_local_order

_SOURCE = re.compile(r'\b(?:documents?|sources?|spreadsheets?|selected files|local knowledge)\b', re.I)
_EXPLICIT_CORPUS = re.compile(
    r'\b(?:from|in|using|with)\s+(?:my|the|these|our)\s+(?:uploaded\s+|selected\s+|registered\s+)?'
    r'(?:(?:maintenance|policy|attached)\s+)?(?:documents?|local sources?|sources?)\b', re.I,
)
_COMPARE = re.compile(r'\b(?:compar(?:e|ing|ison)|contrast|differences?)\b', re.I)
_DRAFT_TOPIC = re.compile(r'\b(?:compar(?:e|ing)|(?:about|on|of)\s)\s*(.+)$', re.I)
_FACT_BOUNDARY = re.compile(r'\s*(?:,\s*(?:and\s+)?|\s+and\s+|;\s*)', re.I)


def workflow_parts(prompt, *, has_sources, has_image):
    if len(prompt) > 1000:
        raise KnowledgeQuestionError
    clauses = artifact_clauses(prompt)
    topic = clauses[0] if len(clauses) == 2 else ''
    if not topic and (match := _DRAFT_TOPIC.search(clauses[-1])):
        topic = match[1]
        if _COMPARE.search(clauses[-1]):
            topic = 'Compare ' + topic
    if has_image and not has_sources and not _SOURCE.search(prompt):
        return ()
    if not topic:
        topic = clauses[-1]
    topic = _EXPLICIT_CORPUS.sub('', topic)
    topic = re.sub(r'^\s*(?:this|the|my)\s+(?:inspection\s+)?(?:image|photo|picture)\s+and\s+'
                   r'(?:(?:the|my)\s+)?(?:attached\s+)?(?:document|source|file)\b', '', topic, flags=re.I)
    topic = re.sub(r'\s*,?\s+(?:and\s+)?(?:identify|highlight|summari[sz]e|explain)\s+'
                   r'(?:(?:the|any|their)\s+)?(?:(?:key|important)\s+)?differences\.?$', '', topic, flags=re.I)
    topic = re.sub(r'\s*(?:and\s+)?(?:use|analyze)\s+(?:this|the|my)\s+'
                   r'(?:inspection\s+)?(?:image|photo|picture)\b', '', topic, flags=re.I).strip(' ,;')
    parts = question_parts(topic or clauses[-1])
    if len(parts) != 1 or not _COMPARE.search(topic):
        return parts
    # Preserve the comparison scope in each independently typed question part.
    tags = entities(topic)
    ordered = []
    for fragment in _FACT_BOUNDARY.split(topic):
        fact = requested_fact(fragment, measurements(fragment))
        if fact and fact.attribute != 'unrecognized' and fragment not in ordered:
            ordered.append(fragment)
    if len(ordered) <= 1:
        return parts
    if len(ordered) > MAX_QUESTION_PARTS:
        raise KnowledgeQuestionError
    suffix = ' for ' + ' and '.join(tags.values()) if tags else ''
    return tuple(question_parts(
        (fragment if _COMPARE.search(fragment) else 'Compare ' + fragment)
        + (suffix if set(entities(fragment)) != set(tags) else '')
    )[0] for fragment in ordered)


def workflows_with_sources(prompt):
    return _EXPLICIT_CORPUS.search(prompt) is not None


class WorkflowConversion(StrictModel):
    part: int = Field(ge=1, le=MAX_QUESTION_PARTS)
    first_citation: int = Field(ge=1, le=MAX_CONTEXT_PASSAGES)
    second_citation: int = Field(ge=1, le=MAX_CONTEXT_PASSAGES)
    conversion: LocalUnitConversionOutput


class WorkflowReceipt(StrictModel):
    steps: list[Literal['retrieve', 'vision', 'vision_unavailable', 'draft']] = Field(min_length=2, max_length=3)
    findings: list[Digest] = Field(min_length=1, max_length=MAX_FINDINGS)
    references: list[Digest] = Field(min_length=1, max_length=MAX_FINDINGS)
    conversions: list[WorkflowConversion] = Field(max_length=2)


class WorkflowCheckpoint(StrictModel):
    findings: list[Finding] = Field(min_length=1, max_length=MAX_FINDINGS)
    answer: str = Field(min_length=1, max_length=65536)
    knowledge: KnowledgeReceipt | None
    conversions: list[WorkflowConversion] = Field(max_length=2)
    vision_unavailable: bool
    steps: list[Literal['retrieve', 'compare', 'vision']] | None = Field(default=None, min_length=1, max_length=3)

    @model_validator(mode='after')
    def validate_steps(self):
        if self.steps is None:  # L18 stored one complete evidence checkpoint.
            return self
        if (self.steps not in (['retrieve'], ['retrieve', 'compare'], ['vision'],
                               ['retrieve', 'vision'], ['retrieve', 'compare', 'vision'])
                or (self.knowledge is not None) != ('retrieve' in self.steps)
                or (self.conversions and 'compare' not in self.steps)
                or (self.vision_unavailable and 'vision' not in self.steps)):
            raise ValueError('Invalid completed workflow steps')
        return self

    def binding(self):
        return self.model_dump(exclude={'steps'} if self.steps is None else None)


@dataclass(frozen=True)
class WorkflowEvidence:
    findings: tuple[Finding, ...]


def finding_digest(finding, *, reference=False):
    return hashlib.sha256(_json(finding.model_dump(exclude={'text', 'kind'} if reference else None))).hexdigest()


def matches_findings(rows, proofs):
    available = Counter(proofs)
    edited = Counter()
    for row in rows:
        reference = finding_digest(row, reference=True)
        if row.kind == 'user_edited':
            edited[reference] += 1
        else:
            key = (finding_digest(row), reference)
            if not available[key]:
                return False
            available[key] -= 1
    remaining = Counter()
    for (_, reference), count in available.items():
        remaining[reference] += count
    return edited <= remaining


def workflow_image_current(state, session, identity, turn_id):
    record = state.engine._memory.read_local_turn(session, identity, turn_id)
    if 'image' not in record:
        return True
    from backend.multimodal.local_images import LocalImageRef

    try:
        ref, _ = state.local_images.get(session, identity, record['image']['ref']['image_id'], turn_id=turn_id)
        return ref == LocalImageRef.model_validate(record['image']['ref'])
    except (KeyError, ValueError):
        return False


def current_workflow_draft(state, session, identity, draft):
    reply = draft_reply(state.engine._memory, session, draft)
    if reply is None:
        return False
    try:
        origin = state.engine._memory.read_local_turn(session, identity, draft.turn_id)
        if not draft.change and origin['draft_scope']['scope']['format'] != draft.format:
            return False
        if not workflow_image_current(state, session, identity, draft.turn_id):
            return False
        if KNOWLEDGE_FIELD in reply.extra_fields:
            receipt = KnowledgeReceipt.model_validate(reply.extra_fields[KNOWLEDGE_FIELD])
            state.local_knowledge.check_scope(identity[0], receipt.scope)
            if state.local_knowledge.public_receipt(identity[0], receipt) is None:
                return False
        proof = WorkflowReceipt.model_validate(reply.extra_fields['local_workflow'])
        if draft.change:
            return matches_findings((row for row in draft.findings if row.turn_id == draft.turn_id),
                                    zip(proof.findings, proof.references))
        return len(draft.findings) == len(proof.findings) == len(proof.references) and all(
            (finding_digest(row, reference=True) == reference if row.kind == 'user_edited' and draft.revision > 1
             else finding_digest(row) == digest)
            for row, digest, reference in zip(draft.findings, proof.findings, proof.references)
        )
    except (KeyError, ValueError, PermissionError):
        return False


async def _dispatch(engine, run, user, step):
    from backend.agent.engine import LocalToolError

    run.check_workflow_active()
    registry = engine._tool_registry
    name = step.tool_name
    if not registry.allows_local_tool(name, user.id, user.role):
        raise LocalToolError('LOCAL_TOOL_UNAUTHORIZED')
    registration = registry.registration_token(name)
    invocation = run.turn_id if name == DRAFT_TOOL else str(uuid.uuid4())
    scope = local_chat_tool_turn.set((run.session_id, user.id, invocation, name))
    enabled = request_tools_enabled.set(True)
    try:
        async def invoke():
            return await engine._dispatch_tool(
                name, step.arguments, session_id=run.session_id, user_id=user.id,
                step_id=invocation, expected_registration=registration,
            )

        if name == DRAFT_TOOL:
            result = await invoke()
            verified = registry.registration_token(name) == registration
        else:
            result, verified = await execute_plan_action(
                name, step.arguments, invoke, registry.get,
                current=lambda: registry.registration_token(name) == registration,
            )
    finally:
        request_tools_enabled.reset(enabled)
        local_chat_tool_turn.reset(scope)
    run.check_workflow_active()
    if not verified or not result.success or result.tool != name:
        raise LocalToolError('LOCAL_TOOL_FAILED')
    return result.result


def _measure(field):
    raw, unit = field['value'], field['unit']
    entries = measurements(raw, topic=field['attribute'])
    if not entries:
        entries = measurements(f'{raw} {unit}', topic=field['attribute'])
    return entries[0] if len(entries) == 1 and entries[0].low == entries[0].high else None


async def _compare(engine, run, user, public):
    from backend.agent.engine import LocalToolError

    findings, conversions = [], []
    for index, part in enumerate(public.get('coverage', ())):
        fields = part.get('fields', ())
        if part['state'] != 'supported' or not _COMPARE.search(part['question']) or len(fields) != 2:
            continue
        left, right = fields
        if (left['attribute'] != right['attribute'] or left['conditions'] != right['conditions']
                or left['citation'] == right['citation']):
            continue
        a, b = map(_measure, fields)
        if a is None or b is None or a.dimension != b.dimension:
            continue
        value = b.low
        if a.unit != b.unit:
            if not (len(conversions) < 2 and a.convertible and b.convertible and abs(value) <= Decimal('1e12')
                    and Decimal(str(float(value))) == value):
                continue
            step = PlanStep(id=f'convert_{index + 1}', description='Normalize the second source value',
                            tool_name='unit_conversion',
                            arguments={'value': float(value), 'from_unit': b.unit, 'to_unit': a.unit})
            plan = AgentPlan(task_id=run.turn_id, objective=part['question'], steps=[step])
            registry = engine._tool_registry
            if engine._plan_validator.validate(plan, tool_lookup=lambda name: registry.get(name)
                                               if name == 'unit_conversion' else None):
                raise LocalToolError('LOCAL_TOOL_INVALID')
            result = LocalUnitConversionOutput.model_validate(await _dispatch(engine, run, user, step))
            conversions.append(WorkflowConversion(part=index + 1, first_citation=left['citation'],
                                                  second_citation=right['citation'], conversion=result))
            order = exact_local_order(a.low, a.unit, b.low, b.unit)
        else:
            order = (a.low > value) - (a.low < value)
        sign = {-1: 'is less than', 0: 'equals', 1: 'is greater than'}[order]
        # Refer to the exact source values. The normalization is a separate tool receipt.
        left_label = ', '.join(left['equipment']) or 'First cited source'
        right_label = ', '.join(right['equipment']) or 'Second cited source'
        text = (f'{left_label} ({left["value"]}) {sign} {right_label} ({right["value"]}).'
                + (f' Compared in {a.unit} using the local converter.' if a.unit != b.unit else ''))
        findings.append(Finding(turn_id=run.turn_id, topic=part['question'], text=text,
                                kind='comparison', coverage='supported'))
    return findings, conversions


async def finish_workflow(engine, run, prompt, model):
    from backend.agent.engine import LocalToolError

    user = run.check_access()
    state = run.app_state
    registry = engine._tool_registry
    args = (None if run.answer_only else
            DraftRequest(turn_id=run.turn_id, attempt=run.attempt, scope=run.draft_scope))
    if not run.tools_enabled or (args and not registry.allows_local_tool(DRAFT_TOOL, user.id, user.role)):
        raise LocalToolError('LOCAL_TOOL_UNAUTHORIZED')
    steps = []
    if run.knowledge_scope is not None:
        steps.append(PlanStep(id='retrieve', description='Retrieve and verify source passages'))
    if run.image_ref is not None:
        steps.append(PlanStep(id='vision', description='Analyze the explicitly attached image'))
    if args is not None:
        steps.append(PlanStep(id='draft', description='Assemble the reviewable draft', tool_name=DRAFT_TOOL,
                              arguments=args.model_dump()))
    plan = AgentPlan(task_id=run.turn_id, objective=prompt, steps=steps)
    if not (1 if run.answer_only else 2) <= len(plan.steps) <= 3 or engine._plan_validator.validate(
            plan, tool_lookup=lambda name: registry.get(name) if name == DRAFT_TOOL else None):
        raise LocalToolError('LOCAL_TOOL_INVALID')

    run.emit('agent_status', 'planning')
    attempt = (run.session_id, run.identity, run.turn_id, run.attempt)
    corpus = state.local_knowledge
    try:
        async with asyncio.timeout(min(90, state.settings.model_timeout_seconds + 15)):
            checkpoint = engine._memory.workflow_checkpoint(*attempt)
            run.check_workflow_active()
            if run.knowledge_scope is not None:
                corpus.check_scope(user.id, run.knowledge_scope)
            if run.image_ref is not None:
                run.image_payload()
            if checkpoint is None or checkpoint.steps is not None:
                checkpoint = await _prepare_workflow(engine, run, user, prompt, model, checkpoint)
            if run.answer_only:
                run.check_workflow_active()
                corpus.check_scope(user.id, run.knowledge_scope)
                engine._memory.record_plain_response(
                    run.session_id, run.identity, checkpoint.answer, producer=KNOWLEDGE_PRODUCER,
                    local_turn=(run.turn_id, run.attempt),
                    local_knowledge=checkpoint.knowledge.model_dump(exclude_none=True),
                )
                return
            run.emit('agent_status', 'drafting')
            await asyncio.sleep(0)
            run.check_workflow_active()
            findings = checkpoint.findings
            draft = next((row for row in state.local_drafts.owned(run.session_id, run.identity)
                          if row.turn_id == run.turn_id), None)
            if draft is None:
                binding = local_draft_scope.set((state, run.identity, args, WorkflowEvidence(tuple(findings))))
                try:
                    draft = LocalDraft.model_validate(await _dispatch(engine, run, user, plan.steps[-1]))
                finally:
                    local_draft_scope.reset(binding)
            run.check_workflow_active()
            if run.knowledge_scope is not None:
                corpus.check_scope(user.id, run.knowledge_scope)
            if run.image_ref is not None:
                run.image_payload()
            if ((draft.session, draft.generation, draft.turn_id, draft.format)
                    != (run.session_id, run.identity[1], run.turn_id, args.scope.format)
                    or draft.evidence or draft.findings != findings or draft.revision != 1 or draft.approval or draft.cancelled
                    or draft.exported or draft.export_digest):
                raise LocalToolError('LOCAL_DRAFT_FAILED')
            proof = WorkflowReceipt(
                steps=['vision_unavailable' if step.id == 'vision' and checkpoint.vision_unavailable else step.id
                       for step in plan.steps], conversions=checkpoint.conversions,
                findings=[finding_digest(row) for row in findings],
                references=[finding_digest(row, reference=True) for row in findings],
            )
            if draft.id not in state.local_drafts.records:
                state.local_drafts.save(draft)
            engine._memory.record_plain_response(
                run.session_id, run.identity,
                checkpoint.answer + '\n\nYour draft is ready in the Drafts panel. '
                'Review and approve its current revision before export.',
                producer=DRAFT_PRODUCER, local_turn=(run.turn_id, run.attempt), local_draft_id=draft.id,
                local_knowledge=checkpoint.knowledge.model_dump(exclude_none=True) if checkpoint.knowledge else None,
                local_workflow=proof.model_dump(),
            )
    except KnowledgeConflict:
        raise LocalToolError('LOCAL_KNOWLEDGE_CHANGED') from None
    except DraftConflict:
        raise LocalToolError('LOCAL_DRAFT_FAILED') from None
    except TimeoutError:
        raise LocalToolError('MODEL_TIMEOUT') from None


async def _prepare_workflow(engine, run, user, prompt, model, checkpoint):
    from backend.agent.engine import LocalToolError

    attempt = (run.session_id, run.identity, run.turn_id, run.attempt)
    steps = list(checkpoint.steps) if checkpoint else []
    findings = list(checkpoint.findings) if checkpoint else []
    conversions = list(checkpoint.conversions) if checkpoint else []
    receipt = checkpoint.knowledge if checkpoint else None
    answer = [checkpoint.answer] if checkpoint else []
    vision_unavailable = checkpoint.vision_unavailable if checkpoint else False
    corpus = run.app_state.local_knowledge

    def save(step):
        nonlocal checkpoint
        run.check_workflow_active()
        steps.append(step)
        checkpoint = WorkflowCheckpoint(findings=findings, answer='\n\n'.join(answer), knowledge=receipt,
                                        conversions=conversions, vision_unavailable=vision_unavailable, steps=steps)
        engine._memory.workflow_checkpoint(*attempt, checkpoint)

    if run.knowledge_scope is not None:
        parts = workflow_parts(run.workflow_prompt or prompt, has_sources=run.knowledge_scope.mode == 'selected',
                               has_image=run.image_ref is not None)
        await asyncio.sleep(0)
        run.check_workflow_active()
        if 'retrieve' not in steps:
            run.emit('agent_status', 'retrieving_evidence')
            contexts = corpus.contexts(user.id, parts, run.knowledge_scope)
            groups = [(part, refs, json.dumps({'passages': list(refs)}))
                      for part, refs in zip(parts, contexts)]
            sourced, evidence = corpus.answer(user.id, run.knowledge_scope, groups)
            receipt = KnowledgeReceipt.model_validate({
                **evidence, 'turn_id': run.turn_id, 'attempt': run.attempt,
                'request_digest': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
            })
            public = corpus.public_receipt(user.id, receipt)
            if public is None:
                raise KnowledgeConflict('Local sources changed while answering.')
            answer.append(sourced)
            findings.extend(_source_findings(run.turn_id, public))
            save('retrieve')
        if 'compare' not in steps and any(_COMPARE.search(part) for part in parts):
            await asyncio.sleep(0)
            run.check_workflow_active()
            corpus.check_scope(user.id, run.knowledge_scope)
            public = corpus.public_receipt(user.id, receipt)
            if public is None:
                raise KnowledgeConflict('Local sources changed while answering.')
            run.emit('agent_status', 'comparing_evidence')
            comparisons, conversions = await _compare(engine, run, user, public)
            findings.extend(comparisons)
            answer.extend(row.text for row in comparisons)
            save('compare')
    if run.image_ref is not None and 'vision' not in steps:
            await asyncio.sleep(0)
            run.check_workflow_active()
            # Only the user's objective goes to vision. Retrieved text is never instructions.
            try:
                observation = await engine._local_image_observation(run, prompt, model)
            except LocalToolError as exc:
                if receipt is None or str(exc) not in ('LOCAL_VISION_MODEL_UNAVAILABLE', 'MODEL_TIMEOUT'):
                    raise
                vision_unavailable = True
                message = 'Image analysis was unavailable. The document findings rely on the cited sources.'
                answer.append(message)
                findings.append(Finding(turn_id=run.turn_id, topic='Image analysis', kind='missing',
                                        text=message, coverage='absent'))
            else:
                findings.append(Finding(turn_id=run.turn_id, topic='Image analysis', text=observation,
                                        kind='visual_interpretation', coverage='visual',
                                        image={**run.image_ref.attachment(True), 'model': model,
                                               'processing': 'completed'}))
                answer.append('Model-generated image observations\n\n' + observation)
            save('vision')
    if not findings or len(findings) > MAX_FINDINGS:
        raise LocalToolError('LOCAL_DRAFT_FAILED')
    return checkpoint
