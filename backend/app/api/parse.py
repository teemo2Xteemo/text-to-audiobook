from __future__ import annotations

import json
from pathlib import Path

from fastapi import Request, UploadFile
from pydantic import ValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.schemas import CreateJobJsonBody
from app.application.jobs import CreateJobCommand
from app.config.settings import Settings
from app.domain.audio import SPEED_DEFAULT
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import OutputFormat

_JSON_CONTENT = "application/json"
_MULTIPART_CONTENT = "multipart/form-data"
_MULTIPART_OVERHEAD = 4096
_READ_CHUNK = 64 * 1024
_ALLOWED_FILE_MIME = frozenset({"application/octet-stream", "text/plain"})


async def parse_create_job(request: Request, settings: Settings) -> CreateJobCommand:
    content_type = (request.headers.get("content-type") or "").lower()
    _assert_content_length(request, settings.max_upload_bytes)
    if content_type.startswith(_JSON_CONTENT):
        return await _parse_json(request, settings.max_upload_bytes)
    if content_type.startswith(_MULTIPART_CONTENT):
        return await _parse_multipart(request, settings.max_upload_bytes)
    raise DomainError(
        ErrorType.INVALID_INPUT,
        "content type must be application/json or multipart/form-data",
    )


def _assert_content_length(request: Request, max_upload_bytes: int) -> None:
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        length = int(raw)
    except ValueError:
        raise DomainError(ErrorType.INVALID_INPUT, "invalid content-length") from None
    if length > max_upload_bytes + _MULTIPART_OVERHEAD:
        raise DomainError(ErrorType.INVALID_INPUT, "upload exceeds size limit")


async def _parse_json(request: Request, max_upload_bytes: int) -> CreateJobCommand:
    body = await request.body()
    if len(body) > max_upload_bytes + _MULTIPART_OVERHEAD:
        raise DomainError(ErrorType.INVALID_INPUT, "upload exceeds size limit")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise DomainError(ErrorType.INVALID_INPUT, "invalid JSON") from exc
    if not isinstance(payload, dict):
        raise DomainError(ErrorType.INVALID_INPUT, "JSON body must be an object")
    if "file" in payload:
        raise DomainError(ErrorType.INVALID_INPUT, "provide exactly one of text or file")
    try:
        parsed = CreateJobJsonBody.model_validate(payload)
    except ValidationError as exc:
        raise DomainError(ErrorType.INVALID_INPUT, _first_pydantic_message(exc)) from exc
    text = _decode_source_text(parsed.text.encode("utf-8"), max_upload_bytes)
    return CreateJobCommand(
        source_language=parsed.source_language,
        target_language=parsed.target_language,
        text=text,
        voice=parsed.voice,
        speed=parsed.speed,
        output_format=parsed.output_format,
    )


async def _parse_multipart(request: Request, max_upload_bytes: int) -> CreateJobCommand:
    form = await request.form()
    text_value = form.get("text")
    upload = form.get("file")
    text_present = isinstance(text_value, str) and text_value.strip() != ""
    file_present = isinstance(upload, StarletteUploadFile) and bool(upload.filename)
    if text_present == file_present:
        raise DomainError(ErrorType.INVALID_INPUT, "provide exactly one of text or file")

    if file_present:
        assert isinstance(upload, StarletteUploadFile)
        text = await _read_upload(upload, max_upload_bytes)
    else:
        assert isinstance(text_value, str)
        text = _decode_source_text(text_value.encode("utf-8"), max_upload_bytes)

    speed_raw = form.get("speed")
    output_raw = form.get("output_format")
    return CreateJobCommand(
        source_language=_form_string(form.get("source_language"), "source_language"),
        target_language=_form_string(form.get("target_language"), "target_language"),
        text=text,
        voice=_optional_form_string(form.get("voice")),
        speed=_parse_speed(speed_raw),
        output_format=_optional_form_string(output_raw) or OutputFormat.MP3.value,
    )


async def _read_upload(upload: UploadFile | StarletteUploadFile, max_upload_bytes: int) -> str:
    _assert_safe_txt_filename(upload.filename)
    _assert_text_mime(upload.content_type)
    chunks: list[bytes] = []
    total = 0
    while True:
        piece = await upload.read(_READ_CHUNK)
        if not piece:
            break
        total += len(piece)
        if total > max_upload_bytes:
            raise DomainError(ErrorType.INVALID_INPUT, "upload exceeds size limit")
        chunks.append(piece)
    return _decode_source_text(b"".join(chunks), max_upload_bytes)


def _decode_source_text(raw: bytes, max_upload_bytes: int) -> str:
    if len(raw) > max_upload_bytes:
        raise DomainError(ErrorType.INVALID_INPUT, "upload exceeds size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DomainError(ErrorType.INVALID_INPUT, "source text must be UTF-8") from exc
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _assert_safe_txt_filename(filename: str | None) -> None:
    if filename is None or not filename.strip():
        raise DomainError(ErrorType.INVALID_INPUT, "filename is required")
    normalized = filename.replace("\\", "/")
    if ".." in normalized.split("/"):
        raise DomainError(ErrorType.INVALID_INPUT, "filename must not contain path traversal")
    name = Path(filename).name
    if name in {".", ".."} or not name.lower().endswith(".txt"):
        raise DomainError(ErrorType.INVALID_INPUT, "file must be a .txt document")


def _assert_text_mime(content_type: str | None) -> None:
    if not content_type:
        return
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime in _ALLOWED_FILE_MIME or mime.startswith("text/"):
        return
    raise DomainError(ErrorType.INVALID_INPUT, "file MIME type must be text")


def _form_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError(ErrorType.INVALID_INPUT, f"{field} is required")
    return value


def _optional_form_string(value: object) -> str | None:
    if value is None or isinstance(value, StarletteUploadFile):
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_speed(value: object) -> float:
    if value is None or value == "":
        return SPEED_DEFAULT
    if isinstance(value, StarletteUploadFile):
        raise DomainError(ErrorType.INVALID_INPUT, "speed must be a number")
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DomainError(ErrorType.INVALID_INPUT, "speed must be a number") from exc


def _first_pydantic_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid request"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    msg = str(first.get("msg", "invalid request"))
    return f"{loc}: {msg}" if loc else msg
