---

name: debug-investigation

description: Use when investigating errors, failing tests, stack traces, crashes, environment issues, unexpected behavior, or broken commands.

---



# Debug Investigation Skill



Use this skill for debugging and error investigation.



## Goal



Find the root cause with evidence before applying fixes.



## Workflow



1. Read the error message carefully.

2. Identify the failing command, file, line, module, or component.

3. Form the smallest plausible hypothesis.

4. Inspect relevant code and configuration before editing.

5. Reproduce the issue when possible.

6. Propose one fix at a time.

7. Run or suggest the smallest verification command.

8. Explain the root cause and prevention.



## Rules



- Do not guess blindly.

- Do not apply broad rewrites for narrow errors.

- Do not change multiple unrelated areas at once.

- Separate environment causes from code causes.

- If logs are incomplete, state what evidence is missing.

- If the issue cannot be reproduced, explain the most likely cause and how to confirm it.



## Output format



Return:



1. Root cause

2. Evidence

3. Fix

4. Verification command

5. Prevention tip



## Common checks



For Python:



- Python version

- virtual environment

- installed packages

- import paths

- environment variables

- dependency conflicts



For Node.js:



- Node version

- npm version

- package manager

- lockfile state

- script definitions

- module format, such as CommonJS or ESM



For Git:



- current branch

- remote URL

- authentication

- uncommitted changes

- upstream branch

