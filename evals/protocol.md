# Forward-Evaluation Protocol

Keep prompts separate from grading knowledge.

1. Create a fresh temporary workspace and materialize only the named fixture.
2. Copy the Skill to a separate path while excluding `evals/`, test outputs, and prior logs.
3. Substitute `{{SKILL_PATH}}` and `{{WORKSPACE}}` in one entry from `prompts.json`.
4. Give the tested Agent only the substituted `prompt`, fixture, permitted environment
   variables, and Skill copy. Do not mention the rubric or expected assertions.
5. Capture the final response, exit statuses, complete command trace, filesystem diff,
   generated artifacts, and fixture-provided remote trace.
6. Give those raw materials, `rubric.md`, and only the matching entry from
   `expected-assertions.json` to an independent grader.
7. Repeat once with the same task and fixture but without access to the Skill to establish
   the baseline. Recreate the fixture before every run.

For remote cases, use the fixture SSH double by default. A live fnOS run requires separate
authorization and a unique disposable application ID. Never use an existing production
application as an evaluation target.
