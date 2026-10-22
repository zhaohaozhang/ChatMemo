# Topic Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, locally computed, citation-backed topic overview for each active contact.

**Architecture:** A focused `topics.py` module classifies messages with a versioned fixed lexicon and builds topic aggregates without persistence. The server exposes those aggregates only for active contacts. The UI presents topic cards and reuses the existing context panel for every cited message.

**Tech Stack:** Python 3.11+ standard library, vanilla HTML/CSS/JavaScript, `unittest`.

**Spec:** `../specs/2026-10-03-topic-ai-contact-lifecycle-design.md`

## Global Constraints

- Run this plan after `2026-10-03-contact-lifecycle.md` because active-contact filtering is a dependency.
- Initial topics are 饮食、工作学习、行程约定、健康、兴趣娱乐、购物金钱、家庭朋友、住处出行.
- One message may belong to multiple topics but only once per topic.
- Unmatched messages remain unclassified.
- Copy must say “记录中提到” and “规则提取”; never infer preference, personality, or diagnosis.
- Topic results are not stored in SQLite; return a fixed rule version.
- This directory is not a Git repository. Use local verification checkpoints.

## Review Focus

- Negation and third-person mentions remain quoted evidence, not a claimed user fact; tested in Task 1.
- Repeated keyword occurrences in one message do not inflate message counts; tested in Task 1.
- Representative messages span distinct dates instead of only a dense recent burst; tested in Task 2.
- Empty and all-unmatched contacts return an honest empty state; tested in Tasks 1 and 3.
- 30,000 long messages remain within a measured local performance budget without persisting derived data; tested in Task 4.

---

### Task 1: Versioned Multi-label Topic Classifier

**Files:**
- Create: `topics.py`
- Create: `tests/test_topics.py`

**Interfaces:**
- Produces: `RULE_VERSION = "1"`
- Produces: `classify_message(message: dict) -> list[str]`
- Produces: `classify_messages(messages: list[dict]) -> dict[str, list[dict]]`

- [ ] **Step 1: Write failing classifier tests**

Use literal Chinese fixtures for each of the eight topics, one multi-label message, repeated keywords, an unmatched message, a negated statement, and a third-person statement. Assert labels only; do not assert inferred likes/dislikes.

- [ ] **Step 2: Verify the tests fail**

Run: `python -m unittest tests.test_topics.TopicClassifierTests -v`

Expected: FAIL because `topics.py` does not exist.

- [ ] **Step 3: Implement classifier functions**

Use an ordered mapping of topic name to fixed terms. Normalize only case and whitespace; do not perform pronoun resolution or sentiment inference. Deduplicate labels per message.

- [ ] **Step 4: Verify the task**

Run: `python -m unittest tests.test_topics.TopicClassifierTests -v`

Expected: PASS.

- [ ] **Step 5: Record a local checkpoint**

Append classifier evidence to `VERIFICATION.md`.

### Task 2: Topic Aggregates and Representative Evidence

**Files:**
- Modify: `topics.py`
- Modify: `tests/test_topics.py`

**Interfaces:**
- Consumes: `classify_messages()` from Task 1
- Produces: `build_overview(messages: list[dict], representatives: int = 5) -> dict`
- Returns: `rule_version`, `classified_messages`, and topics with `name`, `message_count`, `day_count`, `first_at`, `last_at`, `messages`

- [ ] **Step 1: Write failing aggregate tests**

Assert exact counts, inclusive time bounds, distinct calendar-day counts, stable topic order, and at most five representative messages chosen from distinct dates with the latest evidence included.

- [ ] **Step 2: Verify the tests fail**

Run: `python -m unittest tests.test_topics.TopicOverviewTests -v`

Expected: FAIL because `build_overview` is absent.

- [ ] **Step 3: Implement deterministic aggregation**

Sort input by `(timestamp, id)`. Select representatives by newest distinct dates first, then fill remaining slots with newest unused messages. Return original message dictionaries so IDs and source lines remain available.

- [ ] **Step 4: Verify the task**

Run: `python -m unittest tests.test_topics -v`

Expected: PASS.

- [ ] **Step 5: Record a local checkpoint**

Append aggregate evidence to `VERIFICATION.md`.

### Task 3: Topics API and Active-contact Enforcement

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `Store.contact()`, `Store.messages()`, `topics.build_overview()`
- Produces: `GET /api/topics?id=<contact_id>`

- [ ] **Step 1: Write failing API tests**

Assert the response schema and literal counts for a synthetic contact, plus empty-contact, missing-contact, and trashed-contact errors.

- [ ] **Step 2: Verify the tests fail**

Run: `python -m unittest tests.test_server.HTTPTests.test_topics_api tests.test_server.HTTPTests.test_topics_empty_contact tests.test_server.HTTPTests.test_topics_rejects_trashed_contact -v`

Expected: FAIL because the route is absent.

- [ ] **Step 3: Implement the route**

Validate the active contact through `store.contact()`, fetch messages once, build the overview, and return JSON with `Cache-Control: no-store` through the existing response helper.

- [ ] **Step 4: Verify the task**

Run: `python -m unittest tests.test_server.HTTPTests -v`

Expected: PASS.

- [ ] **Step 5: Record a local checkpoint**

Append API evidence to `VERIFICATION.md`.

### Task 4: Performance Boundary

**Files:**
- Modify: `tests/test_topics.py`
- Modify: `topics.py` only if the measured implementation misses the limit

**Interfaces:**
- Consumes: `build_overview()`
- Produces: no new public interface

- [ ] **Step 1: Add a 30,000-message performance test**

Generate fixed-size synthetic messages across the eight topics. Assert completion under 2.0 seconds on the current development machine and verify input messages are not mutated.

- [ ] **Step 2: Run the performance test**

Run: `python -m unittest tests.test_topics.TopicPerformanceTests -v`

Expected: PASS under 2.0 seconds. If it fails, profile term matching and precompile normalized term tuples; do not add persistence.

- [ ] **Step 3: Re-run all topic tests**

Run: `python -m unittest tests.test_topics -v`

Expected: PASS.

- [ ] **Step 4: Record a local checkpoint**

Record elapsed time and machine context in `VERIFICATION.md`.

### Task 5: Topic Overview UI and Citation Navigation

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/style.css`
- Modify: `README.md`
- Modify: `VERIFICATION.md`

**Interfaces:**
- Consumes: `GET /api/topics` from Task 3 and existing `showContext(message_id)`
- Produces: “主题概览” and “原文检索” views with topic cards and evidence links

- [ ] **Step 1: Add view controls and rendering**

Load topics when an active non-empty contact is selected. Render rule version, counts, date range, distinct days, and representative `M<id>` buttons. Preserve search form behavior and keyboard focus.

- [ ] **Step 2: Add honest empty/error states**

Distinguish no messages, no rule matches, loading failure, and trashed contact. Do not use preference or personality copy.

- [ ] **Step 3: Run static and backend tests**

Run: `node --check web/app.js && python -m unittest tests.test_topics tests.test_server -v`

Expected: exit 0 and all tests PASS.

- [ ] **Step 4: Verify with the synthetic browser contact**

Open each topic card, confirm every representative ID opens the existing source/context panel, switch to search and back, refresh, and check narrow and desktop layouts. Do not test on a real contact.

- [ ] **Step 5: Run the full suite and document**

Run: `python -m unittest discover -s tests -v && node --check web/app.js`

Expected: all tests PASS. Update `README.md`, `VERIFICATION.md`, and save a topic-overview screenshot.

