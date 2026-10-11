# Repository development

Build simple, explicit agent tooling with concise instructions, interfaces, help, and output. Minimize maintenance overhead and agent context consumption.

Treat repository development as regular software work. Before changing implementation, distinguish development of this repository from workflow behavior used in other projects.

Favor user intent and common lifecycles over Git CLI coverage. Group related operations, hide details such as remote names when unhelpful, and keep interfaces small and predictable. Preserve existing interfaces unless a change meaningfully simplifies or improves them.

Use the narrowest relevant validation: syntax checks when sufficient, affected tests for behavior changes, and broader tests for cross-cutting changes.
