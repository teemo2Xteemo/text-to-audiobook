# ADR 0001: Language-agnostic architecture

- Status: Accepted
- Date: 2026-09-01

## Context

Requirements mix a Chinese→Vietnamese *example* (overview, UI mock, several ACs) with an explicit rule that source and target languages are user-selected and must not be hard-coded. A multilingual TTS product that encodes one pair in domain logic will have to be rewritten for every additional pair.

## Decision

The domain models **any** `source_language` → `target_language` pair supported by configured providers.

- Domain identifiers are BCP-47 tags (e.g. `zh-CN`, `vi-VN`, `ja-JP`) plus `auto` for source detection.
- Supported languages are the intersection of translation and TTS provider capabilities, not a hard-coded list in business logic.
- UI defaults, if any, are configuration (`DEFAULT_TARGET_LANGUAGE`), never constants in services.
- Provider-specific codes stay in adapters.

Chinese→Vietnamese remains the **primary acceptance example**, not the architecture name.

## Consequences

Agents must not create `translate_zh_to_vi()` or a single global Vietnamese voice. Tests may use zh/vi fixtures. Config may default the UI toward a language the operator prefers.

## Alternatives

- Ship a zh→vi-only MVP and generalize later — rejected; ports are cheaper now than a rewrite after Edge TTS leaks into the domain.
- ISO 639-1 only (`zh`, `vi`) — rejected as the sole identifier; TTS voices are locale-specific (`zh-CN` vs `zh-TW`).
