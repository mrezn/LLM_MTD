# Architecture

`LLM_MTD_eval` is an evaluation and orchestration layer that integrates with:

- `LLM_MTD_emo` through HTTP APIs and scenario files
- `LLM_MTD` conceptually through the LLM-MTD analytical decision model and
  planned hybrid mode

Phase 1 keeps the integration surface narrow:

1. fetch emulator state
2. normalize it
3. generate or simulate an LLM decision
4. validate and adapt the decision
5. save a structured dry-run trial result
