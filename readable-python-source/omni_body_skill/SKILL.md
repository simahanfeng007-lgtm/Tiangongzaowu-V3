# Tiangong v3 Omni Body — Direct Action Reference

All work goes through the single `omni_body` tool. Prefer the implemented
production action directly. Use `system.action_schema` when an action's exact
arguments are uncertain; do not invent action names.

## How to work

**One call, one action.** Pick an action below, fill `target` / `args` /
`workspace`, execute it, inspect `ok` / `success`, then continue. For large
jobs, work in bounded batches and keep going until verification and packaging
are complete.

## Implemented core actions

### File
| action | what it does |
|--------|-------------|
| file.read | Read text or supported file content at `target` |
| file.write | Write/create file; content in `args.content` |
| file.append | Append content to a file |
| file.list | List a directory |
| file.search | Search files/content under a path |
| file.hash | Hash a file |
| file.mkdir | Create a directory |
| file.copy | Copy; destination in `args.destination` |
| file.move / file.rename | Move or rename; destination in `args.destination` |
| file.delete_to_trash | Move file/directory into governed trash; A4 reversible action, no legacy confirmation |

There is no `file.exists` action. Use `file.list`, `file.read`, or
`system.action_schema` and treat a successful result as evidence.

### Code, shell, archive, rollback
| action | what it does |
|--------|-------------|
| code.read / code.write | Read or write source files |
| code.patch_replace | Exact targeted replacement in a source file |
| python.run | Run Python code/script |
| shell.run | Run a shell command; command in `args.command` |
| quality.python_syntax | Compile/syntax validation |
| quality.run_tests | Run project tests |
| git.status / git.diff / git.add / git.commit / git.log | Implemented Git operations |
| zip.create / zip.extract | Create or extract ZIP archives |
| rollback.list / rollback.apply | Inspect or apply recorded rollback |

There is no `code.scan` or `code.diff` action. Use `file.list` / `file.search`
and `git.diff`.

### Documents
| action | what it does |
|--------|-------------|
| docx.create | Create a Word `.docx` from structured content |
| pptx.create | Create a PowerPoint `.pptx` deck from slide specs |
| pptx.read | Inspect slide text, 16:9 dimensions, placeholders, fonts, and meaningful visuals |
| sheet.create / sheet.read | Create or read Excel workbooks |
| pdf.extract_text | Extract text from PDF |
| pdf.create_from_text | Create PDF from text |
| mindmap.create | Create a mind-map artifact |

### Quality and delivery
| action | what it does |
|--------|-------------|
| qc.* | Artifact-specific quality checks |
| rubric.evaluate | Evaluate against a supplied rubric |
| repair.plan | Produce a repair plan from failed checks |
| preview.generate | Generate a delivery preview |
| deliverable.package | Package final artifacts for delivery |

### Search and web
| action | what it does |
|--------|-------------|
| web.search / browser.search_web | Search by keywords in `args.query` |
| web.read | Read and clean a URL from `target` or `args.url` |
| web.fetch / web_readability_extract | Compatibility aliases for URL reading |
| http.get | Direct HTTP GET where applicable |

When `web.search` receives an HTTP(S) URL, the runtime routes it to URL reading.
Error pages, captcha/login pages, empty content, and `content_blocked` are
failures, not completed reads.

### Image, audio, video
| action | what it does |
|--------|-------------|
| image.create_canvas / image.info | Create or inspect an image |
| image.resize / image.crop / image.rotate / image.convert | Image transforms |
| image.add_text / image.compose | Add text or compose images |
| audio.tts / audio.tone / audio.trim / audio.concat | Audio generation/editing |
| video.info / video.cut / video.extract_audio / video.add_audio / video.slideshow | Video operations |

There is no generic local `image.generate` action. Use an implemented local
image action or a configured external action such as `openai_api.image.generate`.

### Knowledge, skill and introspection
| action | what it does |
|--------|-------------|
| learning.ingest | Create a pending learning card from an explicit learning request |
| skill.route / skill.list / skill.get / skill.read | Route or inspect available skills when the model decides they are needed |
| system.capabilities | List runtime capabilities |
| system.action_schema | Get the exact schema for an action |
| system.health | Check tool runtime health |

There is no `knowledge.search` action in the current registry.

## Rules

1. **Never invent actions.** When uncertain, call `system.action_schema` or `system.capabilities`.
2. **Evidence-driven completion.** Do not claim completion without successful tool results and requested verification.
3. **Real artifacts.** Deliveries must produce real local files; package large multi-file output with `deliverable.package`.
4. **Long-chain continuity.** At lease checkpoints, state a concise visible continuation reason and immediately issue the next tool call.
5. **Repair before finish.** Failed tests/QC require repair and rerun, not narrative completion.
6. **Authorization stays host-owned.** A1-A4 actions do not use the retired confirmation flow; never invent `confirmed`, `confirmation`, or capability-grant fields. A5 and hard-deny paths remain blocked by the host/gateway.

## Call shape

```json
{
  "action": "file.write",
  "target": "output.txt",
  "args": {"content": "..."},
  "workspace": "C:\\Users\\xxx\\Desktop"
}
```
