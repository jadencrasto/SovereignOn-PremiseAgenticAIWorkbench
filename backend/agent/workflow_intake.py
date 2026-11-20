"""Typed input slots for the existing local evidence-to-draft workflow."""

import re
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from backend.multimodal.local_images import MAX_IMAGES, LocalImageRef
from backend.rag.local_knowledge import MAX_SOURCES, KnowledgeScope, ScopedSource, knowledge_question
from backend.rag.local_relevance import entities, measurements, requested_fact
from backend.tools.local_drafts import Format, StrictModel, artifact_clauses


_TWO_DOCUMENTS = re.compile(r'\b(?:two|2|both)\s+(?:[\w-]+\s+){0,3}(?:reports|documents|sources|files|records|workbooks)\b', re.I)
_THESE_DOCUMENTS = re.compile(r'\bthese\s+(?:[\w-]+\s+){0,3}(?:reports|documents|sources|files|workbooks)\b', re.I)
_DOCUMENT_GOAL = re.compile(
    r'^\s*(?:please\s+)?(?:compare|contrast|analy[sz]e)\b[^\n]{0,580}\b'
    r'(?:reports?|documents?|sources?|files?|records?|workbooks?)\b', re.I,
)
_COMPARISON_REQUEST = re.compile(r'^\s*(?:please\s+)?(?:compare|contrast)\b', re.I)
_UNSPECIFIED_EXPORT = re.compile(r'\b(?:and|then)\s+export\s+(?:the\s+)?(?:findings|comparison|results)\b', re.I)
_PUMP = re.compile(r'\b(?:the|a|this) pump\b', re.I)
_DIFFERENCES = re.compile(r'\b(?:the|these) differences\s*[.!?]?$', re.I)
_IMAGE = re.compile(r'\b(?:image|photo|picture)\b', re.I)
_SCOPED_IMAGE = re.compile(r'^\s*(?:use|using)\b.*\b(?:image|photo|picture)\b.*\b(?:document|file|source)\b', re.I)
_MEASUREMENTS = {
    'temperature': 'operating temperatures',
    'pressure': 'operating pressures',
    'inspection_interval': 'inspection intervals',
    'torque': 'torque limits',
    'requirements': 'requirements',
}
_FORMATS = {'docx': 'Word report (DOCX)', 'xlsx': 'Spreadsheet (XLSX)', 'pdf': 'PDF report (ASCII text)'}
MAX_CLARIFICATION_REVISION = MAX_SOURCES + MAX_IMAGES + 6


class ClarificationReply(StrictModel):
    revision: int = Field(ge=1, le=MAX_CLARIFICATION_REVISION)
    selection: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(min_length=1, max_length=2)


class WorkflowIntake(StrictModel):
    revision: int = Field(default=1, ge=1, le=MAX_CLARIFICATION_REVISION)
    scope: KnowledgeScope
    choose_sources: bool = False
    source_count: Literal[1, 2] = 2
    image_options: list[LocalImageRef] = Field(default_factory=list, max_length=MAX_IMAGES)
    needs_image: bool = False
    image: LocalImageRef | None = None
    choose_equipment: bool = False
    equipment_options: list[str] = Field(default_factory=list, max_length=24)
    equipment: str | None = None
    choose_measurement: bool = False
    measurement: Literal['temperature', 'pressure', 'inspection_interval', 'torque', 'requirements'] | None = None
    format: Format | None = None
    answer_only: bool = False

    def binding(self):
        # Preserve the signed L19 representation for existing draft plans.
        excluded = set()
        if not self.answer_only:
            excluded.add('answer_only')
        if self.source_count == 2:
            excluded.add('source_count')
        return self.model_dump(exclude=excluded)

    @property
    def reason(self):
        if self.choose_sources:
            return 'sources'
        if self.needs_image and self.image is None:
            return 'image'
        if self.choose_equipment and self.equipment is None:
            return 'equipment'
        if self.choose_measurement and self.measurement is None:
            return 'measurement'
        if self.format is None and not self.answer_only:
            return 'format'
        return None

    def clarification(self):
        if self.reason == 'sources':
            question = 'Which two documents should I compare?' if self.source_count == 2 else 'Which document should I use?'
            return self._question(question, self.source_count,
                                  [(source.source_id, source.label) for source in self.scope.sources])
        if self.reason == 'image':
            return self._question('Which image should I analyze?', 1,
                                  [(ref.image_id, ref.filename) for ref in self.image_options])
        if self.reason == 'equipment':
            return self._question('Which pump should I use?', 1, [(tag, tag) for tag in self.equipment_options])
        if self.reason == 'measurement':
            return self._question('What should I compare or report?', 1, list(_MEASUREMENTS.items()))
        if self.reason == 'format':
            return self._question('Which format should I use for the draft?', 1, list(_FORMATS.items()))
        return None

    def _question(self, question, count, options):
        return {'revision': self.revision, 'reason': self.reason, 'question': question, 'count': count,
                'uses_image': self.image is not None or self.reason == 'image',
                'options': [{'id': value, 'label': label} for value, label in options]}

    def execution_prompt(self, objective):
        objective = _UNSPECIFIED_EXPORT.sub('', knowledge_question(objective) or objective).rstrip()
        if not self.measurement and not self.equipment:
            return objective
        clauses = artifact_clauses(objective)
        topic = (clauses[0] or clauses[-1]).rstrip(' .?!')
        if self.equipment:
            topic += f' for {self.equipment}'
        if self.measurement:
            topic += f' on {_MEASUREMENTS[self.measurement]}'
        return topic


def make_intake(state, session, identity, objective, selection, image, output_format, *, prior_evidence=False):
    question = knowledge_question(objective) or objective
    clauses = artifact_clauses(question)
    document_goal = bool(_DOCUMENT_GOAL.search(clauses[0] or question) or _SCOPED_IMAGE.search(question))
    answer_only = bool(output_format is None and image is None and not _IMAGE.search(question)
                       and not _UNSPECIFIED_EXPORT.search(question) and _COMPARISON_REQUEST.search(question)
                       and (document_goal or selection is not None))
    pump_goal = bool(output_format and _PUMP.search(objective) and not entities(objective))
    differences = bool(output_format and _DIFFERENCES.search(objective))
    if not (document_goal or answer_only or pump_goal or differences and not prior_evidence):
        return None
    corpus = state.local_knowledge
    scope = corpus.bind_scope(identity[0], selection)
    two = bool(_TWO_DOCUMENTS.search(objective))
    implicit_pair = bool(_COMPARISON_REQUEST.search(question) and _THESE_DOCUMENTS.search(objective))
    choose_sources = ((two and (selection is None or len(scope.sources) != 2))
                      or (implicit_pair and len(scope.sources) < 2) or (document_goal and not scope.sources))
    if not scope.sources and not choose_sources:
        return None
    needs_image = document_goal and bool(_IMAGE.search(objective))
    image_options = state.local_images.list(session, identity) if needs_image and image is None else []
    if needs_image and image is None and len(image_options) == 1:
        image = image_options[0]
    if choose_sources and selection is not None:
        scope = corpus.bind_scope(identity[0], None)
    elif two:
        scope = scope.model_copy(update={'mode': 'selected'})
    tags = corpus.workflow_pumps(identity[0], scope) if pump_goal and not choose_sources else []
    fact = requested_fact(objective, measurements(objective))
    choose_measurement = ((answer_only or two or needs_image or differences or pump_goal
                           or document_goal and _COMPARISON_REQUEST.search(question))
                          and (fact is None or fact.attribute == 'unrecognized'))
    if choose_measurement:
        from backend.agent.local_workflow import workflow_parts

        parts = workflow_parts(question, has_sources=True, has_image=image is not None)
        choose_measurement = not any(
            (part_fact := requested_fact(part, measurements(part))) and part_fact.attribute != 'unrecognized'
            for part in parts
        )
    intake = WorkflowIntake(
        scope=scope, choose_sources=choose_sources, source_count=2 if two or implicit_pair else 1 if choose_sources else 2,
        needs_image=needs_image, image=image, image_options=image_options,
        choose_equipment=pump_goal and len(tags) != 1, equipment_options=tags[:24],
        equipment=tags[0] if len(tags) == 1 else None, choose_measurement=choose_measurement, format=output_format,
        answer_only=answer_only,
    )
    # Keep the existing fully specified artifact route and its signed admission format.
    return intake if intake.reason or output_format is None or pump_goal else None


def add_clarification_attachment(intake, attachment):
    if intake.reason == 'sources' and isinstance(attachment, ScopedSource):
        if attachment in intake.scope.sources:
            return intake
        scope = KnowledgeScope(corpus_id=intake.scope.corpus_id, mode=intake.scope.mode,
                               sources=[*intake.scope.sources, attachment])
        change = {'scope': scope}
    elif intake.reason == 'image' and isinstance(attachment, LocalImageRef):
        if attachment in intake.image_options:
            return intake
        change = {'image_options': [*intake.image_options, attachment]}
    else:
        raise ValueError('This clarification does not accept that attachment.')
    return WorkflowIntake.model_validate({**intake.binding(), **change, 'revision': intake.revision + 1})


def clarify_intake(intake, reply):
    question = intake.clarification()
    if (question is None or reply.revision != intake.revision
            or len(reply.selection) != question['count'] or len(set(reply.selection)) != question['count']
            or not set(reply.selection) <= {item['id'] for item in question['options']}):
        raise ValueError('Choose from the current clarification options.')
    choice = reply.selection[0]
    changes = {'revision': intake.revision + 1}
    if intake.reason == 'sources':
        scope = KnowledgeScope(corpus_id=intake.scope.corpus_id, mode='selected',
                               sources=[source for source in intake.scope.sources if source.source_id in reply.selection])
        changes.update(scope=scope, choose_sources=False, source_count=2)
    elif intake.reason == 'image':
        changes['image'] = next(ref for ref in intake.image_options if ref.image_id == choice)
    elif intake.reason == 'equipment':
        changes['equipment'] = choice
    elif intake.reason == 'measurement':
        changes['measurement'] = choice
    else:
        changes['format'] = choice
    return WorkflowIntake.model_validate({**intake.binding(), **changes})
