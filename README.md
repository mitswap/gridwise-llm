# GridWise LLM

![CI](https://github.com/bup-cse-fest-2026/gridwise-llm/actions/workflows/ci.yml/badge.svg)

BUP CSE Fest 2026 Hackathon (Online Preliminary) — GridWise LLM Submission.

## Pipeline Architecture
1. **Pydantic Validation**: Strict schema enforcement (400 on malformed payloads)
2. **LLM Interpreter**: GPT-4o-mini (with automatic rule-based offline fallback for rate-limits/failures)
3. **Guardrail Validator**: Deterministic, strict bounds checking and type clamping
4. **LP Optimizer**: Mathematical formulation (PuLP/CBC) solving for the lowest cost schedule
5. **Replay Validator**: Independent physics/business logic re-simulation to prevent solver hallucinations

## How to Run

1. Make sure Python 3.11+ is installed.
2. Run the automated local reproduction script:
```bash
# Windows
.\scripts\local_reproduce.ps1

# Linux / Mac
bash scripts/local_reproduce.sh
```

## Testing
The test suite mirrors the exact scoring rubric, verifying robust failure handling, 40+ LLM paraphrases, chaotic payloads, property-based physics invariants, and performance limits.

```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio httpx hypothesis
pytest -v
```
