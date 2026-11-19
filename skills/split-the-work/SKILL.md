---
name: split-the-work
description: "Split work across sub-agents, sessions, workers or worktrees, and keep the pieces from colliding. Use when a task is too large for one context, when the user asks for parallel work or to use the colony or Herdr, when another session must take a piece, and when deciding whether to delegate at all."
---

You are the **Master**. You turn the user's request into work for other agents, and you carry the user's words through unchanged. Drift almost always starts with the Master, not with the workers: a spec retold in the Master's own words, a summary put up for approval instead of the real list, a task dropped when the Master's context was compacted. Everything below exists to stop those three.

Delegate parallel investigation, isolated exploration, or a check that must be independent of whoever did the work. Keep a task yourself when it is a few tool calls, or when it needs what you already hold. For one bounded question, brief one sub-agent and skip the pipeline.

## The pipeline

The work moves through **phases**. Most phases follow an **installed skill** by Matt Pocock or pstack. Start each one with the `Skill` tool at its phase; working a phase from memory skips its checks.

1. **Clarify** with `grilling`. Find facts yourself, through a sub-agent; put only decisions to the user, and record each in their words.
2. **Spec** with `to-spec`, or `wayfinder` when the work is too big for one spec. Cover what the user asked for and nothing else; anything more goes under Out of Scope.
3. **Tickets** with `to-tickets`. See below.
4. **Impact** with `blast-radius`, for any ticket that removes or changes an output. Find what reads it first with `trail callers <symbol> --depth all`.
5. **Order**. Count the files two or more tickets may change. Run one ticket at a time, unless they share no file and the user asks for speed. If the user wants parallel work on tickets that share files, show the count and let them choose.
6. **Execute** with `implement-spec`: one sub-agent per ticket, each with `isolation: "worktree"`, each following `tdd`. Use `swarm` for read-only fan-out or a race between approaches. Use colony workers only when the user names colony or Herdr; see below. A ticket that builds a screen uses `find-a-component`, then `design-the-ui`.
7. **Check**. See below.
8. **State** with `handoff`. See below.

Skip a phase whose output already exists, and say so. An approved spec skips Clarify and Spec.

## Where the installed skills differ

They were written for other setups, and they are never edited. Where they disagree with this skill or the user's rules, this skill and the rules win:

- They ask for `/setup-matt-pocock-skills` and an issue tracker. Skip that and work in `.scratch/<feature-slug>/`: `spec.md`, one ticket per file in `issues/NN-<slug>.md`, and the batch file `handoff.md`. When the project has `openspec/`, the spec is the OpenSpec change proposal instead.
- They print questions. Ask every question with the question tool.
- They push branches and open pull requests. Do neither unless the user asks.
- `implement-spec` aims for maximum concurrency. Follow the Order phase instead.
- They test widely and end with `code-review`. Test only what the spec asks for, and offer `tear-it-apart` instead.
- `swarm` uses Cursor parameters. Use the `Agent` tool, with models picked by `rules/delegation.md`.
- When a skill points to another skill, read that one the same way.

## Tickets

A ticket is the unit a worker builds. To the skill's template, add:

- **Spec**: the path, and the requirement quoted exactly. Never retell it.
- **May change**: the files the ticket may touch.
- **Written at**: the commit the ticket was written against.
- **Out of scope**: what it will not do, especially the tempting next step.

Show the user the ticket list itself, not a summary of it, and wait for their approval. If you think the scope should change, recommend it; the user decides.

The worker's brief is the ticket's path plus the user's words, quoted.

Before a worker starts a ticket, run `git diff --stat <Written at>..HEAD -- <its May change files>`. If any of them changed, update the ticket and show the user the change before the worker builds.

## Check

Compare each worker's changed files with its May change list, and send back anything outside it. Prove behaviour in the real app with the project's `verify-<app>` skill. If the project has none, offer `create-verification-skill`; if the app has outgrown it, use `maintain-verification-skill`. A changed screen also gets `review-the-ui`. Check any claim that something works before you pass it to the user.

## State

The **batch file**, `.scratch/<feature-slug>/handoff.md`, is the record of the work. It holds the user's decisions, the decisions they reversed, the open questions, and one state per ticket: assigned, in progress, done, verified, blocked or abandoned. A done ticket says what a person can now see or do. Update it whenever a ticket changes state. Before your context fills, bring it up to date and tell the user to start a fresh Master from it, because a compaction summary loses exactly what this file keeps.

## Colony workers

A **colony worker** is a full Claude session that runs one ticket. Use colony only when the user names colony or Herdr. Pick the tool by where this session runs. Inside Herdr (`test "${HERDR_ENV:-}" = 1`), use `colony`: each worker gets its own pane. Outside Herdr, and only when the user names colony, use `colonybg`: each worker is a `claude --bg` session, and the project must have been opened with `claude` once, because `claude --bg` refuses an untrusted repository. The steps below name `colony`; with `colonybg`, run the same subcommands. Both tools keep the batch's event log, check each brief, and have workers test and review their own work before they report.

1. `colony brief <ticket>` writes a brief template. Fill every section from the ticket: the quoted requirement, the files it owns, what the worker may decide, the model and effort.
2. `colony start <ticket> <brief>` starts the worker. It refuses a brief with a missing section, a placeholder, or a file another open task owns.
3. Run `colony wait` in the background. It returns each worker's outcome: PASS, FAIL, BLOCKED or QUESTION.
4. Answer a QUESTION with the user's words: `colony answer <ticket> "<answer>"`. Never answer it yourself.
5. Confirm a PASS by checking the lines it cites, run the Check phase, then `colony close <ticket>`.

Record each ticket's colony outcome in the batch file.

## Briefing a worker

A good brief gives the goal (the decision the answer informs), the exact inputs, the bound (how many files or calls, and what is out of scope), the shape of the answer, what counts as proof, and what to do when something is missing. Say "fresh context window" when you want a clean read.

A worker that writes text for people runs the writing check. Never ask a worker to do something your own permissions refused.

Other units: message another session with `SendMessage` only for work in this project's folder, after checking the session list, since names repeat across folders.

## Keeping pieces apart

Each file has one owner. In a shared checkout, never run a bare `git stash`; use a WIP commit, or `git stash push -u -m "<tag>"` and apply by SHA. Every result goes in a file; a message carries the path and at most 300 characters. Silence means unread, not agreed.

Wait on the harness's task notification, or a background `colony wait` or `colonybg wait`; do not poll. Stop every process, worker and worktree you started once nothing needs it.

## Reporting

For each piece: who held it, where the result is, and what you checked yourself. Say what a person can now see or do. Report failed and skipped pieces too.
