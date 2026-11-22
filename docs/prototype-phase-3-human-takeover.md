# Prototype Phase 3: Human takeover

Phase 3 implements the local CLI human-takeover protocol over a visible
Chromium window without requiring a remote dashboard.

## Protocol flow

When the agent proposes `human_handoff` or policy returns `require_human`:

1. **Stop the model loop:** `RunSupervisor` transitions `running -> awaiting_human`
   and halts all `AgentBackend.propose()` calls.
2. **Suspend capture and ordinary telemetry:** `StagehandBrowserAdapter.pause()`
   blocks screenshots, DOM inspection, observation, clicks, fills, extraction,
   and downloads. `observe_state()` returns no page data while paused, and the
   supervisor emits no ordinary telemetry while awaiting the human.
3. **Display takeover context in the CLI:** `CliTakeoverHandler` displays the
   requesting task (`task_id` and `objective`), target site (`origin`), reason,
   and instructions, and confirms that capture is paused.
4. **Human interaction in the existing visible window:** `StagehandBrowserAdapter`
   requires `headless=False` when a Phase 3 `takeover_handler` is configured so
   the operator interacts directly with the running browser window.
5. **Explicit return or decline:** `CliTakeoverHandler` requires an explicit
   `return` (`r`) or `decline` (`d`) response; empty or unrecognized input is
   rejected and re-prompted.
6. **Clear transient clipboard and form state:** Before resuming (or revoking on
   decline), the adapter clears the OS clipboard (via `clear_system_clipboard`,
   without reading its contents) and clears transient text/password form inputs
   on open pages without reading or returning field values.
7. **Record only a redacted event:** `handoff_completed` records only
   `authentication_completed`, `control_returned`, `origin` (normalized origin
   only — never query strings, DOM snapshots, or screenshots), and
   `transient_state_cleared`.
8. **Recheck origin and policy before resuming:**
   - If the human declined control, the browser session is revoked and the run
     transitions `awaiting_human -> cancelled`.
   - If the human returned control, `StagehandBrowserAdapter.resume()` verifies
     every open tab's origin against `allowed_origins` (revoking on mismatch),
     and `RunSupervisor` re-evaluates `PolicyEvaluator` with a
     `resume_after_handoff` check before transitioning back to `running`.

## Components

- [takeover.py](file:///Users/pooguy/universal-benevolent-collective/src/ubc_node/takeover.py):
  `TakeoverOutcome`, `TakeoverDecision`, `TakeoverPromptContext`,
  `CliTakeoverHandler`, and `clear_system_clipboard`.
- [contracts.py](file:///Users/pooguy/universal-benevolent-collective/src/ubc_node/contracts.py):
  `TaskAwareHandoffRuntime` optional protocol for passing task context into
  takeover.
- [stagehand_browser.py](file:///Users/pooguy/universal-benevolent-collective/src/ubc_node/stagehand_browser.py):
  visible-window enforcement, transient clipboard/form clearing, redacted
  takeover result, and revocation on decline.
- [supervisor.py](file:///Users/pooguy/universal-benevolent-collective/src/ubc_node/supervisor.py):
  task-aware handoff dispatch, cancellation on human decline, and post-takeover
  policy recheck before resuming.
