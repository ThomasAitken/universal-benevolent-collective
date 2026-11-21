# Prototype Phase 2 bounded agent loop

Phase 2 connects a real model provider behind the provider-neutral
`AgentBackend` contract and expands `RunSupervisor` into a deliberately boring,
hard-bounded execution loop.

## Execution loop

Each iteration of `RunSupervisor.run` follows one fixed sequence:

1. Check cancellation and hard run budgets (duration, steps, model calls,
   tokens, estimated cost).
2. Observe current browser state if the browser runtime implements
   `BrowserStateObserver` (recording `browser_state_observed`).
3. Ask `AgentBackend.propose` for one structured `ActionProposal` and account
   for its reported `ModelUsage`.
4. Validate the proposal through `PolicyEvaluator.evaluate`.
5. Execute the approved browser action, perform a human handoff, or complete.
6. Record the outcome and reset consecutive failure count on success.
7. Repeat until completion, cancellation, policy denial, fatal error, or budget
   exhaustion.

## Hard limits

`RunBudget` enforces all limits outside the model and browser:

| Limit | Field | Enforcement point |
|---|---|---|
| Steps | `max_steps` | Before each loop iteration |
| Model calls | `max_model_calls` | Before each model proposal |
| Model tokens | `max_tokens` | Before the next call (`>=`) and immediately after a call (`>`) |
| Estimated cost | `max_estimated_cost_usd` | Before the next call (`>=`) and immediately after a call (`>`) |
| Wall-clock duration | `max_duration_seconds` | At loop entry, after browser observation, model call, and handoff |
| Browser actions | `max_browser_actions` | Before executing each browser action |
| Downloads | `max_downloads` | Before executing each `download` browser action |
| Human handoffs | `max_handoffs` | Before transitioning to `awaiting_human` |
| Consecutive failures | `max_consecutive_failures` | After recoverable step failures; resets to `0` on any successful step |

Fatal errors (`FatalRunError`, such as `NavigationPolicyError` or
`BrowserSessionInactiveError`, or any failure during `awaiting_human`) terminate
the run immediately without retrying.

## Provider-agnostic backend architecture

Model-agnostic proposal orchestration is separated from provider wire formats:

- `src/ubc_node/agent_backend.py` owns all provider-neutral logic:
  - `StructuredAgentBackend` (implements `AgentBackend` over any
    `StructuredModelProvider`);
  - `PROPOSAL_SYSTEM_INSTRUCTION` and standard JSON Schema
    `PROPOSAL_JSON_SCHEMA` covering only model-safe browser operations
    (`open`, `inspect`, `screenshot`, `observe`, `click`, `fill`, `extract`,
    `tabs`, `new_tab`, `switch_tab`, `close_tab`, `download`), `human_handoff`,
    and `complete` (never `pause`, `resume`, `close`, or `revoke`);
  - `render_proposal_prompt`, which labels browser observations in the history
    as `untrusted_external_data`;
  - `parse_action_proposal`, which validates every response with Pydantic into
    an `ActionProposal` with `ModelUsage`;
  - `ModelPricing`, `ProposalRequest`, and `ModelResponse`.
- `src/ubc_node/gemini_backend.py` is a thin wire adapter implementing
  `StructuredModelProvider` for the Gemini `generateContent` REST API (plus a
  `create_gemini_agent_backend` factory). It sends `x-goog-api-key` in request
  headers rather than query strings, marks `api_key` `repr=False`, translates
  standard JSON Schema into Gemini's `responseSchema`, and returns raw text and
  token counts. Additional providers (e.g. OpenAI, Anthropic, or local models)
  only need to implement `StructuredModelProvider`.
