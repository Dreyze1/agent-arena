# First-run user validation

**Status:** planned. No independent user sessions have been completed.

This protocol tests whether developers who already use multiple coding-agent CLIs can install
Agent Arena, run the same task with two agents, and understand the offline scorecard without help.
Five sessions provide directional product evidence, not a statistically representative result.

## Readiness before recruiting

- Confirm a GitHub-hosted CI run actually executes its steps and passes. A local green run is useful,
  but does not validate the hosted workflow.
- Publish the first package release before counting sessions toward the release onboarding milestone.
  Sessions run from a source checkout are useful pilots; record them separately.
- Prepare a short, non-sensitive task and a disposable repository so participants do not need to
  expose private source code or risk their main checkout.

## Participants

Recruit five developers who are not contributors to this repository and who already use at least
two coding-agent CLIs. Include at least one developer on native Windows and one on Linux or macOS.
Ask each person to bring a small repository they control, or give them the disposable fixture.

## Invitation draft

> I’m testing Agent Arena, an open-source tool for running two coding-agent CLIs on the same task
> and reviewing their checked results locally. Would you spend 45 minutes trying the published
> install and benchmark flow? You can use a disposable repository; you do not need to share code,
> prompts, logs, or credentials. I’ll observe where the instructions or commands get confusing and
> won’t coach you through the setup.

## Session outline

Allow 45 minutes. Ask the participant to think aloud, but do not coach them through the README or
CLI. Stop at 45 minutes if they are blocked and record the blocker instead of taking over.

1. Start from the published installation instructions and install Agent Arena in a fresh virtual
   environment.
2. Run `arena demo` to confirm the installation and see the report format.
3. Use a participant-controlled repository with at least one commit, or create a disposable Git
   repository and make an initial commit; then initialize it with `arena init`.
4. Configure two already-installed and authenticated provider CLIs using their documented command
   profiles. The participant handles provider login in the provider's own tool.
5. Run `arena doctor`, resolve any blocking findings, and start one two-agent benchmark with a
   repository check that can verify the chosen task.
6. Open the scorecard and ask the participant to explain which results passed verification, which
   did not, and what they would do with the patches.

The participant should not share repository contents, prompts, patches, CLI output, logs, or
credentials with the moderator. Record only elapsed time, steps where help was requested, the
blocker category, and the participant's answers. Do not add product telemetry to collect these
measurements.

## Questions to ask

- What did you expect the tool to do at each step?
- Which setup step took the most effort or was unclear?
- Does the scorecard make it clear which result passed the repository's checks?
- Would you use this on another task next week? What would make that answer change?
- What do you currently do to compare coding agents on your own repository?

## Decision thresholds

Treat these as initial go/no-go thresholds and agree on them before recruiting:

- At least four of five participants complete a verified two-agent scorecard within 45 minutes
  without moderator intervention.
- At least four of five correctly distinguish verified results from incomplete or unverified ones.
- At least three of five say they would use the workflow again on a real task.

If a threshold is missed, group the observed blockers, fix the repeated ones, and run another small
round before investing in hosted reports, a leaderboard, or more provider integrations. Publish
the findings as anonymized counts and themes; do not publish participant code or run artifacts.

## Session notes template

```text
Participant ID:
OS:
Agent CLIs already in use:
Install-to-scorecard time:
Finished verified two-agent run: yes / no
Moderator help needed: yes / no; where:
Could explain verified vs unverified: yes / no
Would use again: yes / no; why:
Top blocker:
```
