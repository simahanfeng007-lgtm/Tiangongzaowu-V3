# Design References

The package design follows the current agent-tooling direction of using explicit tool schemas, computer-use harnesses, and standardized tool/resource connections.

- OpenAI API: Using tools, function tools, built-in tools, remote MCP servers.
- OpenAI API: Computer use guide for click/type/scroll/screenshot style computer harnesses.
- OpenAI Codex: Computer Use guidance emphasizing scoped GUI tasks, permissions, and review prompts.
- Anthropic Claude Computer Use docs: screenshot, mouse, keyboard, desktop automation tool shape.
- Model Context Protocol specification: standardized connection between LLM apps and external tools/data.
- LangChain tool documentation: tools as callable functions with well-defined inputs/outputs.

This package intentionally does not copy any proprietary implementation. It implements a local Python action bus plus skill playbooks.


## v3.3 references

See `REFERENCES_V3_3.md` for the expanded delivery pack reference baseline.
