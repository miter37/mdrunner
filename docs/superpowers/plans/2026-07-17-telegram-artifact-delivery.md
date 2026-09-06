# Telegram Artifact Delivery Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make successful artifact delivery from scheduled tasks observable and reliable, and add a temporary artifact-output instruction only when Telegram artifact delivery is enabled.

**Architecture:** The runner will create a short-lived augmented prompt file when `task.notify_artifact` is true, leaving the user's prompt untouched and passing the augmented path through every agent adapter. Artifact delivery will return an explicit result, validate Telegram's HTTP/JSON response, and turn missing/failed delivery into a visible task failure with a log entry. Existing marker parsing and timestamp fallback remain as complementary discovery paths.

**Tech Stack:** Python 3.11+, PyYAML, `urllib.request`, pytest, systemd user timers.

## Global Constraints

- Do not modify the user's original prompt files.
- Append the forced instruction only when `notify_artifact` is true.
- Never claim delivery succeeded without a successful Telegram API response.
- Preserve existing unrelated working-tree changes.
- Do not send a Telegram message during unit tests; mock the network boundary.
- Remove the temporary experimental task and scheduler entry after the live verification.

---

### Task 1: Define the delivery and prompt-injection behavior with failing tests

**Files:**
- Modify: `tests/test_runner.py`
- Modify: `tests/test_telegram.py`

**Interfaces:**
- Tests will establish a temporary prompt is augmented only for `notify_artifact=True`.
- Tests will require `_send_result_artifacts_via_telegram` to report missing files and failed sends rather than silently returning.
- Tests will require `send_document` to reject Telegram JSON responses with `ok: false`.

- [ ] **Step 1: Add a failing test for artifact prompt augmentation**

Assert that a fake agent receives the original prompt plus a `Saved: /absolute/path/...` instruction when artifact delivery is enabled, while the original prompt file remains unchanged and a disabled task receives only the original prompt.

- [ ] **Step 2: Add failing tests for delivery failure propagation**

Cover these cases:

```python
assert delivery.missing_files
assert not delivery.ok
assert "no artifact" in delivery.error.lower()
```

and:

```python
mock_send.return_value = (False, "HTTP Error 403")
assert not delivery.ok
assert "403" in delivery.error
```

- [ ] **Step 3: Add a failing Telegram response test**

Mock `urlopen` to return HTTP 200 with `{"ok": false, "description": "bot was blocked"}` and assert `send_document()` returns `(False, ...)`.

- [ ] **Step 4: Run only the new tests and verify they fail for the intended missing behavior**

Run:

```bash
pytest -q tests/test_runner.py -k 'artifact_prompt or artifact_delivery_failure' tests/test_telegram.py -k 'json_failure'
```

Expected: failures showing the current runner does not augment prompts, delivery does not return a result, and Telegram JSON `ok: false` is treated as success.

---

### Task 2: Implement isolated prompt augmentation

**Files:**
- Modify: `mdrunner/runner.py`
- Modify: `tests/test_runner.py`

**Interfaces:**
- Add a private context manager/helper that yields the original prompt path when delivery is disabled and a temporary augmented prompt path when enabled.
- The helper must clean up the temporary file on both normal completion and process-launch failure.

- [ ] **Step 1: Implement the smallest temporary-prompt helper**

The helper will read the original UTF-8 prompt, append a clearly delimited instruction, write a temporary UTF-8 `.md` file, yield its `Path`, and unlink it in `finally`.

- [ ] **Step 2: Pass the yielded path into `adapter.build_argv`**

Keep all adapters unchanged by changing only the prompt path supplied to them. This covers argv prompts, stdin prompts, and OpenClaw's `--message-file` path uniformly.

- [ ] **Step 3: Run the prompt tests and the existing agent/runner tests**

Run:

```bash
pytest -q tests/test_runner.py tests/test_agents.py
```

Expected: all relevant tests pass, with no original prompt-file mutation.

---

### Task 3: Make Telegram delivery explicit and fail-visible

**Files:**
- Modify: `mdrunner/telegram.py`
- Modify: `mdrunner/runner.py`
- Modify: `tests/test_telegram.py`
- Modify: `tests/test_runner.py`

**Interfaces:**
- `send_document()` returns `(False, error)` for transport errors, HTTP errors, malformed responses, or JSON `ok: false`.
- Add a small delivery result type or equivalent structured return containing `ok`, attempted paths, sent paths, and error text.
- `run_task()` sets a visible error on an otherwise successful agent run when requested artifact delivery has no candidate or has a failed send.

- [ ] **Step 1: Validate Telegram JSON responses**

Read the response body as JSON and require `ok is True`; preserve the API description in the returned error when false.

- [ ] **Step 2: Catch and report delivery failures at the runner boundary**

Collect one error per failed file, write a concise `telegram artifact delivery ...` line to the task log, and update the returned `RunResult.error` without hiding the original agent result.

- [ ] **Step 3: Preserve fallback discovery and make empty discovery explicit**

If no marker path is valid, retain the configured artifact-directory/time-window scan. If it finds nothing while `notify_artifact` is enabled, return a visible delivery failure.

- [ ] **Step 4: Run the complete focused test set**

Run:

```bash
pytest -q tests/test_telegram.py tests/test_runner.py -k 'artifact or telegram'
```

Expected: all focused tests pass; no real Telegram request is made.

---

### Task 4: Verify the live scheduled workflow with a temporary task

**Files:**
- Modify temporarily: `/home/doyoonkim/.config/mdrunner/tasks.yaml`
- Create temporarily: systemd user service/timer through `mdrunner schedule-install`
- Remove after verification: temporary task and timer/service

**Interfaces:**
- Temporary task clones `leader_speaks` execution settings, uses a unique ID, is scheduled daily two minutes after setup, and keeps artifact delivery enabled.

- [ ] **Step 1: Record current task/config/scheduler state**

Save the original task YAML content and current timer status before adding the experiment.

- [ ] **Step 2: Add and install the temporary daily task**

Use the actual live launcher and a time two minutes in the future; verify the timer reports the expected next trigger.

- [ ] **Step 3: Observe the complete run**

Verify systemd starts the service, the task log contains `mode=scheduled`, the agent creates an artifact, and the Telegram delivery log records an API success. Do not infer Telegram success from agent exit code alone.

- [ ] **Step 4: If live delivery fails, use the recorded Telegram error to make one targeted fix and repeat the experiment**

Do not broaden the change or retry blindly; use the exact HTTP/API/network evidence.

- [ ] **Step 5: Remove the temporary task and scheduler entry**

Restore the original `tasks.yaml`, uninstall the temporary scheduler entry, reload systemd, and verify no temporary timer/service remains.

---

### Task 5: Final verification and handoff

**Files:**
- Inspect: `git diff`, task logs, systemd journal, and temporary-task cleanup state

- [ ] **Step 1: Run the full test suite and record pre-existing failures separately**

Run `pytest -q` and distinguish failures introduced by this work from the known test monkeypatch compatibility issue.

- [ ] **Step 2: Verify the live original tasks are unchanged**

Confirm both original task IDs, schedules, prompts, and artifact settings match the pre-experiment snapshot.

- [ ] **Step 3: Verify the implementation diff**

Confirm only the intended source/tests/docs changes are present and unrelated user changes remain untouched.

- [ ] **Step 4: Report evidence, including exact Telegram response status and cleanup result**

Do not report success unless the live Telegram API response and post-test scheduler state are both verified.
