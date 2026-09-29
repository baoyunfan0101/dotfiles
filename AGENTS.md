# Repository development

This project builds agent tooling for disciplined development with low token overhead. Prefer simple, explicit behavior and concise instructions, interfaces, help, and output.

When developing this repository, treat it as a regular software project. It also contains the source of the workflow used by agents in other projects. Decide whether a task concerns repository development or workflow behavior before changing implementation.

Design commands around clear user intent rather than Git CLI coverage. Support common operations and the reasonable lifecycle of each exposed concept. Group related operations under coherent namespaces, hide details such as remote names when they do not help users or agents, and keep interfaces small and predictable. Preserve clear existing interfaces unless a change meaningfully simplifies or improves them.

Use the narrowest relevant validation during development: direct syntax checks when sufficient, affected test modules for behavior changes, and broader tests for cross-cutting changes.
