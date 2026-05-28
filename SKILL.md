---
name: quale
description: Structural codebase analysis for code review, editing, and test verification.
---

# Quale: structural codebase analysis

## When to invoke
When asked to edit, review, debug, refactor, or analyze code.

## Before every edit
Run `quale ec <file>`

Returns: risk level, verification candidates, stable anchor warnings.
Measured: 75% test accuracy, 0.0 extra edits across 1,100 trials.

Use the `verification_mc.candidates` field to find the right test file before editing.

## After every edit
Run `quale vp <file>`

Returns: verification candidates with co-change signal.
Measured: 80% accuracy, best cost/benefit (2.87).

The top candidate is the most likely test file.

## Repo orientation
When first encountering a repo, run `quale o`
Returns: language breakdown, module map, landmark files, recommended workflow.
