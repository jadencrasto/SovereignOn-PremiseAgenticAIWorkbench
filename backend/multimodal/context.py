"""Conversation rules for request-scoped attachments."""

import re
from typing import Literal

from backend.models.base import Message

AttachmentKind = Literal['image', 'txt', 'csv']

IMAGE_REATTACH_MESSAGE = (
    "Earlier images are unavailable for follow-up questions. Reattach the image to ask about it."
)
TEXT_REATTACH_MESSAGE = "Earlier text and CSV files are unavailable. Attach the file again to ask about it."

_IMAGE_REFERENCE = re.compile(
    r"\b(?:this|that|the|my)\s+(?:(?:previous|last|earlier|attached|uploaded)\s+)?"
    r"(?:image|photo|picture|screenshot)\b"
    r"|\b(?:previous|last|earlier|attached|uploaded)\s+(?:image|photo|picture|screenshot)\b",
    re.IGNORECASE,
)
_EARLIER_IMAGE_REFERENCE = re.compile(
    r"\b(?:previous|last|earlier|original|old) (?:image|photo|picture|screenshot)\b"
    r"|\b(?:image|photo|picture|screenshot) (?:i |you )?(?:sent|uploaded|attached) earlier\b",
    re.IGNORECASE,
)
_IMMEDIATE_VISUAL_FOLLOWUP = re.compile(
    r"^(?:and\s+)?(?:describe it\b|look at it\b|"
    r"what(?:'s| is) (?:in|on) it\b|what do you see\b|"
    r"what (?:colou?r|shape) (?:is|was) it\b|(?:can you )?read (?:it|the text)\b)",
    re.IGNORECASE,
)
_TEXT_FILE_REFERENCE = re.compile(
    r"\b(?:this|that|the|my|previous|earlier|last|attached|uploaded)\s+"
    r"(?:(?:previous|earlier|last|attached|uploaded)\s+)?(?:text file|\.txt file|file|document)\b",
    re.IGNORECASE,
)
_EARLIER_TEXT_FILE_REFERENCE = re.compile(
    r"\b(?:previous|earlier|last|original|old)\s+(?:text file|\.txt file|file|document)\b",
    re.IGNORECASE,
)
_NAMED_TXT_REFERENCE = re.compile(r'\b(?:this|that|the|my|attached|uploaded)\s+(?:text file|\.txt(?: file)?)\b', re.IGNORECASE)
_IMMEDIATE_TEXT_FILE_FOLLOWUP = re.compile(
    r"^(?:and\s+)?(?:(?:summarize|summarise|quote|read) it|what (?:does|did) it say"
    r"|show me (?:the|which) source (?:lines|values|passages)(?: you used)?"
    r"|explain why that result matters)\b",
    re.IGNORECASE,
)
_CSV_REFERENCE = re.compile(
    r'\b(?:this|that|the|my|previous|earlier|last|original|old|attached|uploaded)\s+'
    r'(?:(?:previous|earlier|last|original|old|attached|uploaded)\s+)?(?:csv(?: file)?|table|spreadsheet)\b',
    re.IGNORECASE,
)
_EARLIER_CSV_REFERENCE = re.compile(r'\b(?:previous|earlier|last|original|old)\s+(?:csv|table|spreadsheet)\b', re.IGNORECASE)
_NAMED_CSV_REFERENCE = re.compile(r'\b(?:this|that|the|my|attached|uploaded)\s+\.?csv\b', re.IGNORECASE)
_IMMEDIATE_CSV_FOLLOWUP = re.compile(
    r'^(?:(?:and|now)\s+)?(?:how many (?:data )?rows'
    r'|(?:what are|list) (?:the )?(?:column names|columns|headers)'
    r'|what about the (?:first|second|third|last|next)'
    r'|compare (?:it|them|those|these)'
    r'|(?:show|list) (?:only )?(?:those|these)'
    r'|which of (?:those|these)'
    r'|(?:find|calculate|show) their (?:average|sum|minimum|maximum|total)'
    r'|compare (?:the )?first and second results'
    r'|(?:calculate|find) (?:the )?(?:average|sum|total) of (?:these|those) (?:values|readings))\b',
    re.IGNORECASE,
)


def is_image_turn(message: Message) -> bool:
    return message.role == "user" and bool(message.images or message.extra_fields.get("attachments_v1"))


def requires_reattachment(history: list[Message], prompt: str, *, current_attachment: AttachmentKind | None = None) -> bool:
    """Recognize explicit image references and immediate visual follow-ups."""
    if current_attachment is not None:
        refers_to_old_image = _EARLIER_IMAGE_REFERENCE.search(prompt)
        if not refers_to_old_image and current_attachment != 'image':
            refers_to_old_image = _IMAGE_REFERENCE.search(prompt)
        return bool(refers_to_old_image) and any(is_image_turn(message) for message in history)
    if _IMAGE_REFERENCE.search(prompt):
        return any(is_image_turn(message) for message in history)
    if _IMMEDIATE_VISUAL_FOLLOWUP.match(prompt.strip()):
        last_user = next((message for message in reversed(history) if message.role == "user"), None)
        return last_user is not None and is_image_turn(last_user)
    return False


def request_scoped_history(history: list[Message], *, current_image_turn: str | None = None) -> list[Message]:
    """Keep old attachment turns and legacy image payloads out of provider context."""
    return [
        message for message in history
        if not message.images
        and not message.extra_fields.get("text_attachment_v1")
        and (not is_image_turn(message) or (current_image_turn is not None and message.id == current_image_turn))
    ]


def requires_text_reattachment(history: list[Message], prompt: str, *, current_attachment: AttachmentKind | None = None) -> bool:
    """A replacement only satisfies a reference to its own format."""
    if current_attachment is not None:
        if _EARLIER_TEXT_FILE_REFERENCE.search(prompt) and any(_is_file_turn(message) for message in history):
            return True
        if current_attachment != 'txt' and _NAMED_TXT_REFERENCE.search(prompt) and any(
            _is_file_turn(message, 'text/plain') for message in history
        ):
            return True
        return bool(_EARLIER_CSV_REFERENCE.search(prompt) or (
            current_attachment != 'csv' and _NAMED_CSV_REFERENCE.search(prompt)
        )) and any(_is_file_turn(message, 'text/csv') for message in history)

    if _TEXT_FILE_REFERENCE.search(prompt):
        return any(_is_file_turn(message) for message in history)
    if _CSV_REFERENCE.search(prompt) and any(_is_file_turn(message, 'text/csv') for message in history):
        return True
    if re.search(r'\bamong (?:the )?filtered rows\b', prompt, re.I):
        return any(_is_file_turn(message, 'text/csv') for message in history)
    if _IMMEDIATE_TEXT_FILE_FOLLOWUP.match(prompt.strip()):
        last_user = next((message for message in reversed(history) if message.role == "user"), None)
        return last_user is not None and _is_file_turn(last_user)
    if _IMMEDIATE_CSV_FOLLOWUP.match(prompt.strip()):
        last_user = next((message for message in reversed(history) if message.role == "user"), None)
        return last_user is not None and _is_file_turn(last_user, 'text/csv')
    return False


def _is_file_turn(message: Message, mime: str | None = None) -> bool:
    attachment = message.extra_fields.get('text_attachment_v1')
    return message.role == 'user' and bool(attachment) and (
        mime is None or isinstance(attachment, dict) and attachment.get('mime_type') == mime
    )
