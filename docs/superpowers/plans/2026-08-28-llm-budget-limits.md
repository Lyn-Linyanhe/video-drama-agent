# LLM Budget Limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise the DeepSeek request budget enough for long structured screenplay output while making the accepted screenplay input size explicit and configurable.

**Architecture:** Keep the existing OpenAI-compatible client and environment-based configuration. Set the default structured output budget to 32768 tokens, retain the verified product reasoning effort (`high`), and expose an 80000-character screenplay input cap through one config constant used by all script request models and file-import validation.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, OpenAI-compatible async client, pytest.

**Spec:** Latest user request: increase output, reasoning, and input limits for the current DeepSeek screenplay pipeline.

## Global Constraints

- Do not expose API keys in logs, tests, or responses.
- Preserve existing provider-specific behavior for non-DeepSeek providers.
- Do not change stored project data or re-run historical projects.
- The 80000-character input cap is an API validation limit, not a promise that every maximum-size request fits every provider context window.

---

### Task 1: Lock the budget contract with regression tests

**Files:**
- Modify: `tests/test_llm_client.py`
- Create: `tests/test_script_input_limits.py`

**Interfaces:**
- Assert the committed default output budget is 32768 and the committed DeepSeek reasoning default remains `high` when `.env` is not loaded.
- Assert script request models accept 70000-character source text.

- [ ] **Step 1: Write the failing tests**
- [ ] **Step 2: Run the focused tests and confirm they fail on the current 8192/high/60000 defaults**

### Task 2: Implement configurable request limits

**Files:**
- Modify: `src/config.py`
- Modify: `src/utils/llm.py`
- Modify: `src/api/context.py`
- Modify: `src/api/routes/scripts.py`
- Modify: `src/api/routes/settings.py`

**Interfaces:**
- Add `LLM_MAX_INPUT_CHARS` with default 80000.
- Keep `LLM_MAX_TOKENS` and `LLM_REASONING_EFFORT` as the single client budget controls.
- Refresh all three budget values during settings hot reload; script Pydantic field limits take effect after process reload.

- [ ] **Step 1: Wire the config constant into all screenplay request validators and import validation**
- [ ] **Step 2: Refresh the LLM module globals during settings hot reload**
- [ ] **Step 3: Run focused tests and confirm they pass**

### Task 3: Apply local defaults and verify runtime behavior

**Files:**
- Modify: `.env`
- Modify: `.env.example`

**Interfaces:**
- Set `LLM_MAX_TOKENS=32768`, keep `LLM_REASONING_EFFORT=high`, and set `LLM_MAX_INPUT_CHARS=80000`.

- [ ] **Step 1: Update the local and example configuration without printing secrets**
- [ ] **Step 2: Restart the local API service**
- [ ] **Step 3: Verify `/api/settings` metadata, a 70000-character validation probe, and a tiny DeepSeek structured request**
- [ ] **Step 4: Run the full pytest suite**

### Self-review checklist

- [ ] No credential values are included in the plan, test output, or code comments.
- [ ] `LLM_MAX_INPUT_CHARS` is used consistently for JSON script endpoints and TXT/DOCX/MD extraction.
- [ ] Existing high-effort regression expectations remain valid; no default reasoning change is made.
- [ ] The service process is restarted after configuration changes.
