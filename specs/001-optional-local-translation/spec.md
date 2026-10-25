# Feature Specification: Optional Local Literary Translation

**Feature Branch**: `001-optional-local-translation`

**Created**: 2026-10-01

**Status**: Draft

**Input**: User description: "GitHub issue #12 (https://github.com/teemo2Xteemo/text-to-audiobook/issues/12). The free on-device translator used for the current demo produces literary translations that cannot be narrated: later parts of a passage are dropped, and names, medical terms, and idioms are wrong. The owner wants an optional higher-quality literary translator that the operator runs, on this computer or another computer they control, with no paid account. The packaged default stays the offline stand-in. The free on-device translator stays available when the operator selects it. A paid hosted translator is a later option. The accepted architecture decision for this choice is already recorded; this specification defines the product behavior that decision requires."

## Clarifications

### Session 2026-10-01

- Q: Where may story text be sent while the optional literary translator is selected? → A: Only to a translator the operator runs, on this computer or another computer they control. Paid and public translation services are outside this feature.
- Q: Does one translator choice apply to every new story until you change it, or does each story choose its own translator? → A: One lab-wide choice. It applies to every new story until the operator changes it.
- Q: If you change the lab translator choice while a story is unfinished, which translator do the remaining pieces use? → A: The unfinished story keeps the translator and model it started with. The new lab choice applies only to stories created afterward.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Narrate a story the default translator mangles (Priority: P1)

An operator pastes or uploads a story and asks for an audiobook in another language. The free default translator cuts off later sentences and mistranslates character names, specialized terms, and idioms, so the spoken result is unusable. The operator turns on the optional literary translator they run on this computer or another computer they control. That one lab choice applies to every new story until the operator changes it. The operator keeps the same source and target languages and receives a complete translation they can send on to narration and speech.

**Why this priority**: This is the reason the feature exists. If the optional translator does not restore a full, narratable literary translation, the rest of the feature has no user value.

**Independent Test**: Run the documented sample story that the default translator shortens and mistranslates, with the optional local translator selected. Compare the result with a careful literary reference for that sample. The optional result includes the whole sample, and names, the medical term, and the idiom are recognizable without a manual rewrite of those items.

**Acceptance Scenarios**:

1. **Given** the optional local translator is selected and reachable, and the source language is already known, **When** the operator submits the documented sample story for translation into a supported target language, **Then** every part of the sample appears in the translation and the later portion is not dropped.
2. **Given** the same sample and a careful literary reference, **When** a reviewer compares character names, the medical term, and the idiom, **Then** those items in the optional translation are close enough to the reference to narrate without rewriting them.
3. **Given** a story in any language pair the optional translator supports, **When** the operator requests that pair, **Then** the translation uses the requested source and target languages. One demonstration pair is only a quality check, not the only path the product supports.
4. **Given** a completed translation from the optional translator, **When** the pipeline continues, **Then** narration still applies the existing punctuation and rhythm rules to that translated text, and translation does not rewrite the text into spoken delivery.
5. **Given** the optional local translator is selected, **When** a story is translated, **Then** the story text is sent only to a translator the operator runs, on this computer or another computer they control. Paid and public translation services are outside this feature.
6. **Given** the operator inspects logs and failure messages for that job, **When** translation succeeds or fails, **Then** those messages omit the story text and any credentials.
7. **Given** the lab translator choice is the optional literary translator, **When** the operator creates another new story and does not attach a translator choice to that submission, **Then** that story uses the same lab choice.

---

### User Story 2 - Keep the packaged default (Priority: P2)

An operator who does not want extra setup creates an audiobook with the offline stand-in that ships as the packaged default. They do not install the optional literary model, do not create a paid account, and do not change any translator choice. An operator who instead selects the free on-device translator can finish the same story without the literary model.

**Why this priority**: The no-account path is the product’s baseline. The literary upgrade is optional and must not become a requirement for a normal audiobook.

**Independent Test**: Create a short audiobook job with the translator choice left at the packaged default. The job completes with the offline stand-in, and the optional literary model is never required. A second job, created after the operator selects the free on-device translator, also completes without that literary model.

**Acceptance Scenarios**:

1. **Given** a new setup with the translator choice unchanged, **When** the operator creates a job for a short story, **Then** translation uses the offline stand-in and the job does not ask for a paid account, the optional literary model, or a translator choice on the submission.
2. **Given** the lab translator choice is still the packaged default, **When** the operator creates a second new story, **Then** that story uses the same offline stand-in.
3. **Given** the operator selects the free on-device translator, **When** they create a new story on a machine where the optional literary model is not installed, **Then** that story uses the free on-device translator and still does not ask for the literary model.
4. **Given** the optional translator is not installed on the machine, **When** the operator uses the packaged default, **Then** that absence does not block the default job.
5. **Given** a fresh product install, **When** the operator looks for the literary model inside the product itself, **Then** the model files are not included; the operator’s separate local translator supplies them.

---

### User Story 3 - Change models and recover from local failures (Priority: P3)

The operator points the product at a different literary model, or the local translator is down, missing its model, or too slow. An unfinished story keeps the translator and model it started with. Work that already succeeded stays finished. Failed pieces explain what the operator needs to fix. A story created after the change uses the new choice, and does not reuse the earlier story’s translation as its own.

**Why this priority**: Literary quality depends on which model is selected, and local setup will fail in ordinary lab use. Safe reuse and clear failures protect long stories from being regenerated or silently wrong.

**Independent Test**: Translate one piece of a story, then change the lab choice and continue that story. Its next piece still uses the translator and model it started with. A new story created after the change uses the new choice, and the first story’s stored translation is not presented as the new story’s result. In a separate drill, a failed piece is retried on the original story (service off, model missing, unsupported language, piece too long) and keeps that story’s original translator.

**Acceptance Scenarios**:

1. **Given** some pieces of a story already have a valid translation, **When** a later piece fails because the local translator is unreachable, the model is missing, the language is unsupported, the source language is not yet known, or the piece is too long, **Then** those successful pieces are kept, and the retry uses the translator and model that story started with.
2. **Given** a story started under one translator and model and still has unfinished pieces, **When** the operator changes the lab translator or model, **Then** the unfinished story keeps the translator and model it started with.
3. **Given** the operator changed the lab translator or model after an earlier story was created, **When** the operator creates a new story with the same text, **Then** the new story uses the new lab choice, and the earlier story’s stored translation is not presented as the new story’s result.
4. **Given** the optional translator returns extra commentary or formatting around the translation, **When** the piece is accepted, **Then** the stored text is the translation itself.
5. **Given** a piece is longer than the literary model can translate in one request, **When** translation runs, **Then** the product reports failure for that piece and does not return a shortened translation as success.
6. **Given** the optional literary translator is selected for a job, **When** that job translates, **Then** only that translator runs, and the default heavy translator is not loaded alongside it.
7. **Given** a typical lab machine with about 16 GB of memory and no operator override, **When** the optional translator is first selected, **Then** the smaller literary model is the choice in effect. A larger model is used only after the operator selects it, and the largest published size is not the lab default.

---

### Edge Cases

- The local literary translator is not running, or the operator has not installed the chosen model.
- The request exceeds the time allowed for one piece.
- The source or target language is outside the optional translator’s supported set.
- The source language is still unresolved (detection has not chosen a language yet).
- The piece is empty or only whitespace.
- The model answers with notes, quotes, or markup in addition to the translation.
- The operator changes the lab translator or model after part of a story has already been translated. That story keeps the translator and model it started with, including retries of failed pieces. Stories created after the change use the new lab choice.
- The machine does not have enough memory to run the free heavy translator and the literary translator together.
- The local translator responds with an empty translation.
- The operator runs the literary translator on another computer they control, and points this product at that translator.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: An operator MUST be able to set one lab-wide translator choice: the packaged offline stand-in, the free on-device translator, or an optional literary translator that they run on this computer or another computer they control. That choice MUST apply to every new story until the operator changes it. A story MUST keep the translator and model in effect when that story was created. That recorded pair is the story’s pin, and it covers retries of failed pieces. A story submission MUST NOT carry its own translator choice.
- **FR-002**: The packaged default MUST remain the offline stand-in translator. A story on that default, and a story whose lab choice is the free on-device translator, MUST be completable without a paid translation account and without the optional literary model installed.
- **FR-003**: While the optional literary translator is selected, story text MUST be sent only to a translator the operator runs, on this computer or another computer they control. Paid and public translation services are outside this feature.
- **FR-004**: For every piece the optional translator accepts, the result MUST cover the full source text of that piece. The product MUST NOT treat a shortened result as success.
- **FR-005**: On the documented sample story that exposed dropped text, wrong names, a wrong medical term, and a wrong idiom, the optional translation MUST be narratable against a careful literary reference for those items without a manual rewrite of those items.
- **FR-006**: Source language and target language MUST be chosen per job from languages the selected translator supports. The feature MUST support every such pair, and MUST NOT limit the product to the sample pair used for the quality check.
- **FR-007**: Translation MUST produce target-language text only. Spoken narration MUST keep today’s punctuation and rhythm rules and MUST remain a separate step from translation.
- **FR-008**: The product MUST fail a piece with an operator-visible reason when the local translator cannot be reached, the chosen model is missing, the request times out, the language is unsupported, the source language is not yet known, the piece is too long for one request, or the translator returns an empty result. Pieces that already succeeded MUST remain completed.
- **FR-009**: Each stored translation MUST be tied to the translator and model that produced it. A story created after the operator changes that choice MUST be translated for the new choice, and MUST NOT be given an earlier story’s stored translation as its own result. The change MUST NOT move an unfinished story onto the new choice.
- **FR-010**: Selecting the optional literary translator MUST run that translator alone for the job. The default heavy translator MUST NOT be loaded at the same time.
- **FR-011**: Literary model files MUST be supplied by the operator’s local translator. The product MUST NOT ship those model files inside its own install.
- **FR-012**: Logs and operator-facing errors MUST omit story text, credentials, and addresses that contain secrets.
- **FR-013**: The smaller literary model MUST be the comfortable lab default. A larger model MAY be selected when the operator wants higher quality and only one heavy translator is running. The largest published model size is outside the lab default.

### Key Entities

- **Translator choice**: One lab-wide setting: the packaged offline stand-in, the free on-device translator, or the optional local literary translator plus its model size. It applies to every new story until the operator changes it. A story does not carry a separate translator choice.
- **Story piece**: A stable portion of the input with a known source language, a target language, and at most one current successful translation for a given translator and model.
- **Translation result**: The full target-language text for a piece, bound to the translator and model that produced it, so a later choice cannot be mistaken for an earlier one.
- **Audiobook job**: The operator’s request to turn a story into speech. It keeps the pin: the lab translator and model in effect when the story was created. Progress rules stay as they are today. A later lab change applies to stories created afterward.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the documented sample story, a reviewer confirms that 100% of the sample text is present in the optional translation, and that the character names, medical term, and idiom match a careful literary reference closely enough to narrate without rewriting those items.
- **SC-002**: An operator completes translation of a short story with the optional local translator without creating a paid account, and the story is sent only to a translator the operator runs on this computer or another computer they control.
- **SC-003**: An operator completes the same short story with the packaged offline stand-in, on a machine where the optional literary model is not installed, without any extra translator setup. The same story also completes when the operator has selected the free on-device translator and the literary model is still not installed.
- **SC-004**: In failure drills for an unreachable translator, a missing model, an unsupported language, an unknown source language, an empty result, and an oversized piece, 100% of cases report a specific reason and leave previously successful pieces unchanged.
- **SC-005**: In a check of at least one lab-choice change made while a story is unfinished, that story’s remaining and retried pieces still use the translator and model it started with. A new story created after the change uses the new choice, and the earlier story’s stored text is not presented as the new story’s result.
- **SC-006**: For a translated passage that is held constant, narration punctuation and rhythm match the rules used before this feature. Translation does not add a separate spoken rewrite.

## Assumptions

- The owner has already accepted a local open-weight literary model, run by the operator on this computer or another computer they control, as the first optional translator. A paid or public hosted translator remains a later feature, after there is budget for it.
- The packaged default stays the offline stand-in translator. The free on-device translator remains available when the operator selects it. Neither path requires the optional literary model.
- The quality sample is one story previously compared with a careful literary reference (including a hosted-assistant translation used only as that reference). That pair is an acceptance check. It does not define which languages the product supports.
- A typical lab machine has about 16 GB of memory. The smaller model is the default lab pick. A mid-size model is allowed when quality matters and the free heavy translator is not also loaded. The largest model size is not a lab default.
- Jobs, piece boundaries, resume of successful pieces, narration, and speech stay as they are today.
- The operator already has, or can install, the local translator separately, on this computer or another computer they control. This feature connects to it and does not redistribute model files.
- Reachability, timeouts, and the local translator’s address are operator configuration. No secret is required for a translator the operator runs. Paid and public translation services are outside this feature.
- The accepted architecture decision for this feature stays in force. This specification does not reopen the choice of a paid translator as the default.
