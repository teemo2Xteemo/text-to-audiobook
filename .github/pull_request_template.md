<!-- "Closes #N" auto-closes the issue on merge; use "Refs #N" for partial work. Say what is deliberately out of scope. -->
Closes #

## Summary
<!-- What changes for users/operators and why. One bullet per behavior change. -->
-

## How to verify
<!-- Steps a reviewer can follow in a few minutes. Screenshots for UI changes. -->
1.

## Tests
- Backend: `pytest -m "not integration"` → ; `ruff check` / `ruff format --check` clean
- Frontend: `npm test` → ; `npm run typecheck` clean
- Not run: <!-- e.g. real Edge TTS / FFmpeg / LLM end to end -->

## Risk, deploy & rollback
<!-- Chunking, cache keys or checkpoint format changed? Existing failed jobs safe to retry?
     New env vars, compose changes? Can we roll back by reverting this PR? Write "None" if none. -->

## Checklist
- [ ] Acceptance criteria of the linked issue are met (or the gap is listed above)
- [ ] No hard-coded source/target language or language pair in business logic
- [ ] Domain code calls `TranslationProvider` / `TTSProvider` ports, not vendor APIs
- [ ] Long work stays in workers; successful chunks are not regenerated on retry
- [ ] Uploads/filenames treated as untrusted; no user input in shell commands; no secrets
- [ ] Docs updated where behavior changed (README, `.env.example`, ADR)
