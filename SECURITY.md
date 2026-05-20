# Security Notes

This project is designed as a local engineering tool, not as a hosted multi-tenant service.

## Intended Use

- Run the Web Review Workbench on `127.0.0.1`.
- Analyze repositories you trust or have permission to inspect.
- Treat generated comments, patches, and review output as engineering assistance, not as an authoritative security audit.

## Current Safety Boundaries

- Uploaded zip files are extracted under `.macr_uploads/`.
- Upload size, extracted size, file count, path traversal, and symlink checks are enforced.
- Uploaded work directories are cleaned after the background job completes.
- The tool does not expose authentication or multi-user access control.

## Do Not

- Expose the local Web Review Workbench directly to the public internet.
- Commit `.env`, API keys, private traces, private repositories, or customer code.
- Run patches from untrusted repositories without reviewing them first.

## Production Hardening Still Needed

- Persistent task queue.
- Container or OS-level sandboxing.
- Authentication and authorization.
- Per-run CPU, memory, file, and wall-clock limits.
- Duplicate PR comment update/collapse strategy.
