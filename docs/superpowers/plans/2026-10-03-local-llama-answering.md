# Local llama.cpp Answering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing evidence-backed AI answer flow easy to configure and verify against a user-run local llama.cpp server.

**Architecture:** Extend the current generic Chat Completions transport with a no-chat connection probe and stricter citation enforcement. Add a localhost preset and connection status to the existing settings dialog. The app never starts or stops llama.cpp, never silently falls back to cloud, and keeps local search usable when the model fails.

**Tech Stack:** Python 3.11+ standard library `urllib`, llama.cpp OpenAI-compatible API, vanilla HTML/CSS/JavaScript, `unittest`.

**Spec:** `../specs/2026-10-03-topic-ai-contact-lifecycle-design.md`

## Global Constraints

- Run this plan after `2026-10-03-contact-lifecycle.md` so trashed contacts cannot supply evidence.
- Default local endpoint is `http://127.0.0.1:8080/v1`; default model is `local-model`; no API key is required.
- The app connects to an already-running server and never manages `llama-server.exe` or GGUF files.
- The connection probe sends no chat record, question, filename, contact name, or API key in the URL.
- Answers require at least one valid `[M<number>]` citation belonging to the selected active contact.
- Failure never triggers an automatic cloud request or a retry that resends evidence.
- This directory is not a Git repository. Use local verification checkpoints.

## Review Focus

- An endpoint with embedded credentials, query, fragment, redirect, or non-loopback HTTP remains rejected; tested in Task 1.
- A probe does not include user evidence and distinguishes connectivity from malformed Chat Completions output; tested in Task 1.
- A syntactically valid citation from a different or trashed contact is rejected before transport; tested in Task 2.
- A model answer with no citations is rejected rather than shown with a warning; tested in Task 1.
- Timeout, HTTP error, invalid JSON, oversized output, and stopped server preserve local evidence without automatic retry; tested in Tasks 1 and 3.

---

### Task 1: Probe and Strict Citation Transport

**Files:**
- Modify: `ai.py`
- Modify: `tests/test_server.py` (`ModelTests`)

**Interfaces:**
- Produces: `validate_config(config: dict) -> tuple[str, str, str, str]`
- Produces: `test_connection(config: dict) -> dict`
- Modifies: `answer(question: str, evidence: list[dict], config: dict) -> dict` to require at least one valid citation

- [ ] **Step 1: Write failing transport tests**

Add tests named `test_probe_sends_no_chat_data`, `test_probe_rejects_malformed_response`, `test_answer_without_citation_rejected`, `test_redirect_rejected`, and `test_timeout_does_not_retry`. Assert the mock receives exactly one request and probe content contains only a fixed synthetic readiness prompt.

- [ ] **Step 2: Verify the tests fail**

Run: `python -m unittest tests.test_server.ModelTests -v`

Expected: FAIL on missing probe/config function and citationless acceptance.

- [ ] **Step 3: Implement shared validation and probe**

Extract endpoint/model/key validation without weakening current rules. Probe with `stream=false`, `max_tokens=8`, a fixed non-private prompt, and a 10-second timeout. Accept any non-empty assistant string and return `status`, `destination`, and `model`; do not log response text.

- [ ] **Step 4: Enforce citations in answers**

After existing invalid-ID validation, raise `ValueError` when the citation set is empty. Keep the 12-message and 24,000-character limits and 60-second single attempt.

- [ ] **Step 5: Verify the task**

Run: `python -m unittest tests.test_server.ModelTests -v`

Expected: PASS with one request per call.

- [ ] **Step 6: Record a local checkpoint**

Append transport evidence to `VERIFICATION.md`.

### Task 2: Model Test Route and Active Evidence Enforcement

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py` (`HTTPTests`)

**Interfaces:**
- Consumes: `ai.test_connection()`, active-only `Store.contact()` and `Store.messages()`
- Produces: `POST /api/model/test`
- Tightens: `POST /api/answer`

- [ ] **Step 1: Write failing HTTP tests**

Add `test_model_probe_route_sends_no_records`, `test_answer_rejects_other_contact_evidence`, and `test_answer_rejects_trashed_contact_evidence`. Use the local mock model server and literal synthetic records.

- [ ] **Step 2: Verify the tests fail**

Run: `python -m unittest tests.test_server.HTTPTests.test_model_probe_route_sends_no_records tests.test_server.HTTPTests.test_answer_rejects_other_contact_evidence tests.test_server.HTTPTests.test_answer_rejects_trashed_contact_evidence -v`

Expected: FAIL because the route/active checks are missing.

- [ ] **Step 3: Implement route and ownership checks**

Route model tests through `ai.test_connection(body["config"])`. For answers, resolve the active contact first, then build available evidence only from that contact. Keep explicit `allow_send is True` confirmation.

- [ ] **Step 4: Verify the task**

Run: `python -m unittest tests.test_server.HTTPTests tests.test_server.ModelTests -v`

Expected: PASS.

- [ ] **Step 5: Record a local checkpoint**

Append API evidence to `VERIFICATION.md`.

### Task 3: Local llama.cpp Preset and Connection State UI

**Files:**
- Modify: `web/index.html:68-78`
- Modify: `web/app.js:225-278`
- Modify: `web/style.css`
- Modify: `README.md`

**Interfaces:**
- Consumes: `POST /api/model/test` and existing `/api/answer`
- Produces: local preset, explicit connection test, local/cloud location label

- [ ] **Step 1: Add local preset fields**

Add a “使用本地 llama.cpp” control that fills endpoint `http://127.0.0.1:8080/v1`, model `local-model`, and blank key. Keep manual compatible providers available.

- [ ] **Step 2: Add connection-test state machine**

States are idle, testing, local-connected, cloud-connected, and failed. Disable repeated tests while pending. Show that the probe sends no chat records. Do not save settings until validation succeeds or the user explicitly chooses manual configuration.

- [ ] **Step 3: Preserve the evidence confirmation**

For localhost, label the confirmation “发送到本机 127.0.0.1”; for remote HTTPS, retain the full destination warning. Never auto-send after a successful probe.

- [ ] **Step 4: Run static checks**

Run: `node --check web/app.js`

Expected: exit 0.

- [ ] **Step 5: Verify against the mock API in browser**

Confirm preset values, successful and failed probe states, no automatic answer request, citationless-answer fallback, and that local search remains usable. Save a settings screenshot.

- [ ] **Step 6: Record a local checkpoint**

Update `README.md` and `VERIFICATION.md`.

### Task 4: Optional Real llama.cpp Smoke Test and Full Regression

**Files:**
- Modify: `VERIFICATION.md`
- No production file change unless the verified protocol differs from the mock contract

**Interfaces:**
- Consumes: user-run `http://127.0.0.1:8080/v1/chat/completions`
- Produces: verification evidence only

- [ ] **Step 1: Run all automated checks before starting a model**

Run: `python -m unittest discover -s tests -v && node --check web/app.js`

Expected: all tests PASS and JavaScript exits 0.

- [ ] **Step 2: Ask the user before starting external model software**

The app does not manage llama.cpp. If the user wants a real smoke test, have them start their existing server with their chosen model and parameters, or explicitly authorize the known local command. Do not assume GPU-layer/context settings.

- [ ] **Step 3: Test the non-private probe**

Use the settings “测试连接” action. Expected: connected status naming `127.0.0.1` and `local-model`; no chat evidence sent.

- [ ] **Step 4: Test with synthetic evidence only**

Use `examples/小林-虚构示例.txt`, ask “之前说喜欢喝什么”, and confirm the answer includes only IDs from the selected evidence. Do not use the user's real contacts for the smoke test.

- [ ] **Step 5: Stop only processes started for this test**

If a process was explicitly started for the smoke test, stop that exact PID. Never stop a pre-existing llama-server process.

- [ ] **Step 6: Record final evidence**

Record model executable/model path only if already public in project configuration; never record keys, raw private text, or full model responses.

