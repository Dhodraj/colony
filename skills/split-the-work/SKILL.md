---
name: split-the-work
description: "Run work too big for one context as tickets across sub-agents, sessions or colony workers, and keep the pieces from colliding. Use when the work needs more than one worker, the user asks for parallel build work, or the user names colony or Herdr."
---

# Split the work

You are the **Master**. You turn the user's request into tickets for workers, carry the user's words through unchanged, and keep every task in the **batch file**, not only in your context.

## Triggers

- One bounded question, not a build → brief one sub-agent with the sections below and skip the pipeline.
- About to restate a requirement in your own words → quote it, with its spec path.
- About to put a summary of the tickets up for approval → show the ticket files themselves.
- Tempted to add work the user did not ask for → put it under Out of Scope and recommend it. The user decides.
- A fact is missing → find it yourself or through a sub-agent. Ask the user only for decisions, and record each answer in their words.
- Two tickets may change the same file → run them one after another. Run in parallel only when they share no file and the user asks for speed. The user wants parallel work on tickets that share files → show the shared-file count and let them choose.
- About to start a worker → run the staleness check under Tickets first.
- About to call `Agent` → pass `model:`, picked with the `who-builds` skill where installed. Without it the sub-agent runs your model.
- A worker reports done → compare its changed files with the ticket's **May change** list and send back anything outside it. Read the lines it cites as proof. Do not pass its claim to the user unchecked.
- A worker asks a question → ask the user with the question tool, then send the user's words to the worker. Never answer it yourself.
- Your permissions refused an action → do not ask a worker to do it.
- Handing a piece to another session → check the session list and message only a session in this project's folder; names repeat across folders. Every result goes in a file; send its path. Silence means unread, not agreed.
- Waiting on a worker → wait on the task notification, or a background `colony wait`. Do not poll.
- About to run `git stash` in a shared checkout → use a WIP commit, or `git stash push -u -m "<tag>"` and apply it by SHA.
- Context getting full, no colony workers → bring the batch file up to date and tell the user to start a fresh Master from it. A compaction summary loses what that file keeps.
- The user names colony or Herdr → read [colony.md](colony.md) and run the workers with it.
- A skill named here is not installed → do that step as this file describes it, say which skill was missing, and do not install it without the user's consent.

## The pipeline

Most **phases** run an installed skill. Start it with the `Skill` tool at its phase; from memory, you skip its checks. Skip a phase whose output exists, and say so. An approved spec skips Clarify and Spec.

1. **Clarify** with `grilling`.
2. **Spec** with `to-spec`, or `wayfinder` when the work is too big for one spec. Cover what the user asked for and nothing else.
3. **Tickets** with `to-tickets`, plus the fields under Tickets. Wait for the user's approval of the list.
4. **Impact** with `blast-radius`, for any ticket that removes or changes an output. Find what reads that output first.
5. **Order**. Count the files two or more tickets may change, and order the tickets by the trigger above.
6. **Execute** with `implement-spec`: one sub-agent per ticket, each with `isolation: "worktree"`, each following `tdd`. Use `swarm` for read-only fan-out or a race between approaches. A ticket that builds a screen uses `find-a-component`, then `design-the-ui`.
7. **Check**. Prove the behaviour in the real app with the project's `verify-<app>` skill. If the project has none, offer `create-verification-skill`. If the app has outgrown it, use `maintain-verification-skill`. A changed screen also gets `review-the-ui`.
8. **State** with `handoff`, into the batch file.

## Where the installed skills differ

They were written for other setups. Do not edit them. Where they disagree with this skill, this skill wins:

- They ask for `/setup-matt-pocock-skills` and an issue tracker → skip both. Work in `.scratch/<feature-slug>/`: `spec.md`, one ticket per file in `issues/NN-<slug>.md`, and the batch file `handoff.md`. When the project has `openspec/`, the spec is the OpenSpec change proposal.
- They print questions → ask each one with the question tool.
- They push branches and open pull requests → do neither unless the user asks.
- `implement-spec` aims for maximum concurrency → follow the Order phase.
- They test widely and end with `code-review` → test what the spec asks for, and offer `tear-it-apart`.
- `swarm` uses Cursor parameters → use the `Agent` tool.
- A skill points to another skill → read that one the same way.

## Tickets

A **ticket** is the unit one worker builds. Add these fields to the `to-tickets` template:

- **Spec**: the spec's path, and the requirement quoted exactly.
- **May change**: the files the ticket may touch.
- **Written at**: the commit the ticket was written against.
- **Out of scope**: what it will not do, especially the tempting next step.

Before a worker starts a ticket, run `git diff --stat <Written at>..HEAD -- <May change files>`. If any file changed, update the ticket and show the user the change before the worker builds.

Brief a sub-agent with these sections: Goal, Inputs, the bound (tool calls, and what is out of scope), Acceptance criteria, Owns, You may decide, what to do when an input is missing, and Result. For a ticket, Inputs holds the ticket's path and the user's words, quoted. Say "fresh context window" when you want a clean read.

## The batch file

`.scratch/<feature-slug>/handoff.md` holds the user's decisions, the decisions they reversed, the open questions, and one state per ticket: assigned, in progress, done, verified, blocked or abandoned. A done ticket says what a person can now see or do. Update it each time a ticket changes state.

## Report

For each piece: who held it, where the result is, and what you checked yourself. Say what a person can now see or do. List failed and skipped pieces. Close every worker and worktree you started once nothing needs it.
