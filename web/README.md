# Local evidence workbench

The static evidence UI ships from the trusted Python package's
`macr/api/evidence_ui/` assets. This directory does not duplicate those files.

Start explicitly with `agent-review evidence-ui`. It binds only loopback,
prints a short-lived local session URL and never opens a browser automatically.
See [the workbench guide](../docs/evidence-workbench.md) for inputs, local
history/export, cancellation, resource and verification boundaries.

The broader auxiliary agent trace viewer remains separate from this static
source-reading workbench.

