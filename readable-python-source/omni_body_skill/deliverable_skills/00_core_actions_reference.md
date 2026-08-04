# Omni Body Core Actions Reference

Use `omni_body` with a concrete `action`, optional `target`, and structured `args`.

## Common Actions

- `system.capabilities`: list available actions.
- `system.health`: inspect runtime health.
- `file.list`: list files under a directory.
- `file.read`: read a text or binary preview.
- `file.write`: write a file with rollback snapshot.
- `docx.create`: create a Word document.
- `pptx.create`: create a PowerPoint deck.
- `sheet.create`: create an Excel workbook.
- `pdf.extract_text`: extract text from a PDF.
- `quality.python_syntax`: compile Python files for syntax errors.

## Learning Boundary

`learning.ingest` creates only a pending learning card and requires a host-verified learning intent token. It must not compile, activate, register, or release learned tools.
