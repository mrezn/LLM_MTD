# Experiment Protocol

Phase 1 protocol:

1. choose a scenario ID from the emulator scenario registry
2. build a normalized state from emulator APIs or offline scenario data
3. generate a single LLM defender decision in dry-run mode
4. validate and adapt the decision to the supported emulator action set
5. save raw output, trace metadata, and a structured trial result
