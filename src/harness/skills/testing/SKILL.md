---
name: testing
description: Write tests that prove something and fail for the right reason.
triggers: [test, tests, testing, coverage, untested, flaky, "write a test", "add a test"]
steps:
  - Read how this project tests: the runner, the fixtures, the conventions
  - Decide what the test must prove and the narrowest level that proves it
  - Write it and watch it fail for the right reason
  - Make it pass and run the whole suite
---

# Testing

A test is a claim about behaviour that a machine can check. Write the claim first.

## Learn the project's way

Find the runner and how it is invoked, the fixtures and helpers the existing tests
lean on, and the naming and layout they follow. New tests should look like they were
always there. Do not introduce a second style, a second runner, or a new dependency to
test something the existing ones can test.

## Decide what to prove

Name the behaviour in a sentence a person would recognise: "a child whose parent is not
listed stays where it was" rather than "test nested_threads". Pick the narrowest level
that can prove it: a pure function over a unit, the boundary over a component, the
process only when the claim is about the process. One claim per test; a test that
checks five things reports one.

## Watch it fail

Run the new test before the change it is for, or with the change reverted, and read
the failure. It must fail because the claim is false, not because of an import, a
fixture, or a typo. A test that has never failed has proved nothing.

## Make it pass, then run everything

Run the whole suite, not the one file, and read any new failure as information rather
than noise. Report what the test proves, what it deliberately does not cover, and the
exact command that runs it.
