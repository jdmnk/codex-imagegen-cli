---
name: context-checkpoint
description: Use only when the user explicitly asks to persist context, prepare for compaction, create a handoff, preserve agent state, summarize progress, resume later, or make future sessions continue seamlessly.
---

Create or update `AGENT_CONTEXT.md`.

The goal is to preserve important task context across compaction, new threads, agent restarts, or handoffs to another agent.

Do not write hidden chain-of-thought. Do not claim to preserve private reasoning. Preserve the reusable working state: conclusions, constraints, decisions, rationale, evidence, current status, and next actions.

## On-demand only

This is an on-demand skill. Never call it automatically.

Use this skill only when the user explicitly asks to checkpoint, persist,
compact, summarize, hand off, resume, continue later, or write current working
state into `AGENT_CONTEXT.md`.

Do not invoke this skill just because:

- the task is long;
- context has accumulated;
- compaction might happen;
- a milestone or decision happened;
- the agent is about to stop, pause, or hand off work.

Those are good moments to suggest a checkpoint if appropriate, but the user
decides when to run this skill.

## Output file

Write to:

`AGENT_CONTEXT.md`

If the file already exists, update it instead of blindly appending. Keep the active context concise and current. Remove stale details unless they are still useful.

## Agent instruction files

If an agent instruction file already exists, update it to tell future agents to
look for `AGENT_CONTEXT.md` before starting work.

Common names include:

- `AGENTS.md`
- `AGENT.md`
- `CLAUDE.md`
- `CODEX.md`
- `.cursorrules`
- `.cursor/rules/*`

When updating an instruction file:

1. Read the existing file first.
2. Preserve existing instructions and formatting where practical.
3. Add one concise instruction if an equivalent reminder is not already present.
4. Do not create a new agent instruction file unless the user explicitly asks.
5. Do not duplicate reminders on repeated checkpoint updates.

Suggested wording:

```md
Before starting work, check whether `AGENT_CONTEXT.md` exists. If it does, read it first to understand the current objective, constraints, decisions, evidence, risks, and next steps.
```

## Freshness marker

At the very top of `AGENT_CONTEXT.md`, write:

```md
Checkpoint-Updated-At: YYYY-MM-DDTHH:MM:SSZ
```

Use current UTC time so future sessions can judge freshness.

Do not claim the checkpoint is ready unless this timestamp was updated during this checkpoint pass.

## Required structure

`AGENT_CONTEXT.md` must contain these sections:

```md
Checkpoint-Updated-At: YYYY-MM-DDTHH:MM:SSZ

# Agent Context

Task status: active | paused | blocked | complete
Confidence: high | medium | low

## Current Objective
What the agent is trying to accomplish now.

## User Preferences and Constraints
Stable user preferences, explicit instructions, style preferences, technical constraints, and non-goals.

## Project / Environment Facts
Repo, branch, runtime, framework, package manager, relevant services, important commands, and assumptions verified from files.

## Current Implementation State
What has already been changed or discovered. Include exact files, functions, modules, APIs, configs, migrations, verification steps, or docs touched.

## Key Decisions
List decisions with short rationale.
Use this format:
- Decision:
  Rationale:
  Status: accepted | tentative | superseded

## Evidence and Results
Commands run, checks performed, logs inspected, errors observed, outputs that matter, and what they imply.

## Known Issues / Risks / Blockers
Bugs, failing checks, uncertain assumptions, missing access, unresolved design questions, edge cases, or risks.

## Rejected Approaches / Do Not Repeat
Approaches already tried or intentionally avoided, with short reason.

## Next Steps
Exact next actions in order. Each step should be actionable by a fresh agent.

## Resume Prompt
A short ready-to-paste prompt for the next session. It must tell the next agent to read `AGENT_CONTEXT.md` before acting, because the detailed handoff lives in this file.
```

## Quality rules

- Prefer facts found in files, command output, user messages, or visible conversation.
- Preserve rationale, not hidden reasoning.
- Be specific enough that a fresh agent can continue without reading the whole prior thread.
- Include file paths and command names when known.
- Mark uncertainty explicitly.
- Distinguish verified facts from assumptions.
- The resume prompt must explicitly tell the next agent to read `AGENT_CONTEXT.md` before acting.
- Do not store secrets, credentials, API keys, private tokens, or sensitive personal data.
- Do not include irrelevant chat history.
- Do not include emotional commentary or praise.
- Keep the active file under about 200 lines unless the task is unusually complex.
- If older context is still useful but too bulky, move it under `## Archived Context` or create `AGENT_CONTEXT_ARCHIVE.md`.

## Update behavior

When updating an existing `AGENT_CONTEXT.md`:

1. Read the existing file first.
2. Preserve still-relevant context.
3. Remove or mark stale context.
4. Update `Checkpoint-Updated-At` to current UTC time.
5. Update `Task status`.
6. Add new decisions, evidence, risks, and next steps.
7. Rewrite the resume prompt.
8. Update any existing agent instruction file with an `AGENT_CONTEXT.md` reminder if needed.
9. Ensure the result is self-consistent.

## Validation checklist

Before finishing, verify that:

- A fresh agent would know what to do next.
- Important user constraints are captured.
- Important files and commands are listed.
- Current status is clear.
- Blockers and uncertainties are explicit.
- The resume prompt is usable.
- The top timestamp was updated.
- Any existing agent instruction file points future agents to `AGENT_CONTEXT.md`.
- No hidden chain-of-thought or secrets are included.

## Final response to user

After writing the checkpoint, respond briefly:

- Confirm `AGENT_CONTEXT.md` was created or updated.
- Mention whether an existing agent instruction file was updated.
- Mention the most important preserved items.
- Mention unresolved blockers, if any.
