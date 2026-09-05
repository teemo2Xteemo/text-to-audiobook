from app.domain.ports import NarrationProcessor


def narrate_chunk(text: str, *, language: str, processor: NarrationProcessor) -> str:
    return processor.process(text, language)
