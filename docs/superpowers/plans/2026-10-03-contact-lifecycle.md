# Contact Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a recoverable contact trash, exact-name permanent deletion, and safe import handling for trashed contacts.

**Architecture:** Add a nullable deletion timestamp to contacts and make active-only access the default at the storage boundary. Restore preserves every ID; permanent deletion explicitly removes dependent rows in one SQLite transaction. HTTP routes expose lifecycle actions, while the UI requires clear confirmation and never auto-restores or auto-purges.

**Tech Stack:** Python 3.11+ standard library, SQLite, vanilla HTML/CSS/JavaScript, `unittest`.

**Spec:** `../specs/2026-10-03-topic-ai-contact-lifecycle-design.md`

## Global Constraints

- Preserve all existing contact, message, source, occurrence, and message IDs during migration and restore.
- Normal search, context, source download, export, topics, and AI must exclude trashed contacts.
- The trash is never automatically purged.
- Permanent deletion requires the exact current contact name and executes atomically.
- Same-name contacts remain distinct; uncertain matches are never auto-restored or merged.
- This directory is not a Git repository. Replace commit steps with local checkpoints unless the user later initializes Git.

## Review Focus

- Existing databases without `deleted_at` migrate once without changing rows or IDs; tested in Task 1.
- A message/source URL captured before trashing cannot bypass active-contact checks; tested in Task 3.
- Permanent deletion that fails after deleting one child table rolls back all changes; tested in Task 2.
- A duplicate file or same name in the trash yields a choice instead of implicit restore; tested in Task 4.
- Two trashed contacts with the same name remain independently restorable and purgeable by ID; tested in Task 2.

---

### Task 1: Schema Migration and Active Contact Boundary

**Files:**
- Modify: `store.py:8-75`
- Test: `tests/test_memory.py`

**Interfaces:**
- Produces: `Store.contacts(include_deleted: bool = False) -> list[dict]`
- Produces: `Store.contact(contact_id: int, include_deleted: bool = False) -> dict`
- Produces: schema version `1` with `contacts.deleted_at TEXT NULL`

- [ ] **Step 1: Write failing migration tests**

Add `test_existing_database_migrates_contacts_without_changing_ids` and `test_active_contact_lookup_excludes_trashed_rows`. Create a version-0 database with an existing contact, open it with `Store`, and assert the original ID/name remain while `deleted_at` exists.

- [ ] **Step 2: Verify the tests fail**

Run: `python -m unittest tests.test_memory.MemoryTests.test_existing_database_migrates_contacts_without_changing_ids tests.test_memory.MemoryTests.test_active_contact_lookup_excludes_trashed_rows -v`

Expected: FAIL because there is no migration or active-only lookup.

- [ ] **Step 3: Implement migration and lookup**

Add `SCHEMA_VERSION = 1` and `Store._migrate(con: sqlite3.Connection) -> None`. Use `PRAGMA table_info(contacts)` before `ALTER TABLE`, then set `PRAGMA user_version=1` inside the initialization transaction. Make `contacts()` active-only by default and centralize single-contact validation in `contact()`.

- [ ] **Step 4: Verify the task**

Run: `python -m unittest tests.test_memory -v`

Expected: PASS with unchanged existing parser/import tests.

- [ ] **Step 5: Record a local checkpoint**

Record the passing command and changed files in `VERIFICATION.md`; do not initialize Git implicitly.

### Task 2: Trash, Restore, and Atomic Purge Storage Operations

**Files:**
- Modify: `store.py:40-210`
- Test: `tests/test_memory.py`

**Interfaces:**
- Consumes: `Store.contact()` from Task 1
- Produces: `trash_contact(contact_id: int) -> dict`
- Produces: `restore_contact(contact_id: int) -> dict`
- Produces: `purge_contact(contact_id: int, exact_name: str) -> dict`
- Produces: `trash() -> list[dict]`

- [ ] **Step 1: Write failing lifecycle tests**

Add tests named `test_trash_hides_contact_but_preserves_rows`, `test_restore_preserves_all_ids`, `test_purge_requires_exact_name_and_removes_dependents`, `test_purge_rolls_back_on_mid_transaction_failure`, and `test_same_name_trashed_contacts_are_independent`. For rollback, install a SQLite trigger in the test database that raises before deleting a source, call the real purge method, and assert all rows remain.

- [ ] **Step 2: Verify the tests fail**

Run: `python -m unittest tests.test_memory.MemoryTests.test_trash_hides_contact_but_preserves_rows tests.test_memory.MemoryTests.test_restore_preserves_all_ids tests.test_memory.MemoryTests.test_purge_requires_exact_name_and_removes_dependents tests.test_memory.MemoryTests.test_purge_rolls_back_on_mid_transaction_failure tests.test_memory.MemoryTests.test_same_name_trashed_contacts_are_independent -v`

Expected: FAIL because lifecycle methods do not exist.

- [ ] **Step 3: Implement storage operations**

Set/clear `deleted_at` for trash/restore. Purge inside `BEGIN IMMEDIATE`, deleting occurrences, messages, sources, then the contact. Validate the exact name before any deletion. Return contact/message/file counts for confirmation and success messages.

- [ ] **Step 4: Verify the task**

Run: `python -m unittest tests.test_memory -v`

Expected: PASS, including injected rollback and same-name cases.

- [ ] **Step 5: Record a local checkpoint**

Append evidence to `VERIFICATION.md`.

### Task 3: Lifecycle HTTP Routes and Stale URL Protection

**Files:**
- Modify: `server.py:51-150`
- Modify: `store.py:143-205`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: lifecycle methods from Tasks 1-2
- Produces: `GET /api/trash`
- Produces: `POST /api/contact/trash`, `/api/contact/restore`, `/api/contact/purge`

- [ ] **Step 1: Write failing route and access tests**

Add tests for trash/list/restore, exact-name purge, and requests to `/api/context`, `/api/source`, `/api/export`, `/api/search`, and `/api/answer` after trashing. Assert stale URLs return 400 and no private bytes.

- [ ] **Step 2: Verify the tests fail**

Run: `python -m unittest tests.test_server.HTTPTests -v`

Expected: FAIL on missing routes and stale URL access.

- [ ] **Step 3: Implement active ownership checks and routes**

Make `Store.context()` and `Store.source()` join through an active contact. Replace server list scans with `store.contact()`. Add the four lifecycle routes while retaining Host, Origin, content-type, and `X-Memory-Client` checks.

- [ ] **Step 4: Verify the task**

Run: `python -m unittest tests.test_server.HTTPTests -v`

Expected: PASS; trashed records are inaccessible outside `/api/trash` and restore/purge actions.

- [ ] **Step 5: Record a local checkpoint**

Append route verification to `VERIFICATION.md`.

### Task 4: Import Conflict Detection Against Trash

**Files:**
- Modify: `store.py`
- Modify: `server.py`
- Modify: `web/app.js:173-224`
- Test: `tests/test_memory.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `Store.trash_matches(name: str = "", digest: str = "") -> list[dict]`
- Extends: `/api/preview` response with `trash_matches`

- [ ] **Step 1: Write failing match tests**

Cover exact source SHA-256, trimmed same name, unrelated names/files, and two same-name trashed contacts. Assert only IDs and aggregate counts are returned, never raw content.

- [ ] **Step 2: Verify the tests fail**

Run: `python -m unittest tests.test_memory.MemoryTests.test_trash_matches tests.test_server.HTTPTests.test_preview_reports_trash_matches -v`

Expected: FAIL because conflict detection is absent.

- [ ] **Step 3: Implement matching and preview response**

Calculate the upload digest before preview response construction. Match exact file hash or case-sensitive trimmed name. Keep “restore”, “import as new”, and “cancel” as explicit UI choices; never select restore automatically.

- [ ] **Step 4: Verify the task**

Run: `python -m unittest tests.test_memory tests.test_server -v`

Expected: PASS with no automatic restore.

- [ ] **Step 5: Record a local checkpoint**

Append conflict verification to `VERIFICATION.md`.

### Task 5: Contact Lifecycle UI and Browser Verification

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/style.css`
- Modify: `README.md`
- Modify: `VERIFICATION.md`

**Interfaces:**
- Consumes: lifecycle and preview routes from Tasks 3-4
- Produces: contact action menu, trash dialog/list, restore action, exact-name purge dialog

- [ ] **Step 1: Add the minimal UI states**

Add “移入回收站” on the selected contact, “回收站” in the sidebar, counts in confirmations, and exact-name input for purge. Disable submit while a request is pending and return focus to a stable control after dialog close.

- [ ] **Step 2: Run static checks**

Run: `node --check web/app.js`

Expected: exit 0.

- [ ] **Step 3: Verify with a disposable browser contact**

Create a synthetic contact, import the synthetic example, trash it, verify it vanishes from search/topics/export, restore it, then trash and permanently delete it by exact name. Confirm a wrong name cannot purge. Do not use the user's real contacts.

- [ ] **Step 4: Run the full suite**

Run: `python -m unittest discover -s tests -v && node --check web/app.js`

Expected: all tests PASS and JavaScript exits 0.

- [ ] **Step 5: Document the result**

Update `README.md` and `VERIFICATION.md`; save a screenshot of the trash confirmation and restored contact.

