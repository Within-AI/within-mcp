# within-mcp

> Public plugin/extension distribution for the Within MCP server.

📚 **Developer documentation:** <https://developers.within.ai/>

`within-mcp` packages [Within](https://www.within.ai/) as an installable
plugin for AI assistant clients that speak the [Model Context Protocol](https://modelcontextprotocol.io/).
Once installed, the plugin lets your assistant query your organization's processes,
explore the Process Index knowledge graph, and ground answers in how your business
actually runs — not generic guesses.

This repository is the canonical public surface for two install paths:

- **Claude Code / Claude** — Anthropic's plugin ecosystem.
- **ChatGPT / Codex** — OpenAI's app ecosystem.
- **Gemini CLI** — Google's CLI extension ecosystem.
- **Microsoft Copilot** — a custom Copilot Studio agent (no official connector yet).


## What it does

The plugin connects your AI assistant to the Within MCP server at
`https://api.within.ai/mcp`. Tools the assistant gets access to
include `search`, `fetch`, process-hierarchy navigation, evidence retrieval, and
Context Graph traversal. The full skill prompt lives at
[`skills/within-process-context-graph/SKILL.md`](./skills/within-process-context-graph/SKILL.md).

## Install - Claude.ai

Customize -> Connectors -> Search -> Within -> Connect

> (NOTE: your organization might have to approve this connector, and you should be added as a user into Within for this to work)

## Install - ChatGPT

Apps -> Search -> Within -> Connect

> (NOTE: your organization might have to approve this app, and you should be added as a user into Within for this to work)

## Install — Claude Code

From a Claude session, run these two commands one after the other.

**1. First**, install the marketplace:

```text
/plugin marketplace add Within-AI/within-mcp
```

**2. Next**, install the plugin:

```text
/plugin install within@within
```

## Install — Codex

Codex splits this into two steps. First, from your **shell**, register the
Within marketplace with Codex:

```bash
codex plugin marketplace add Within-AI/within-mcp
```

Then, from a **Codex session**, run:

```text
/plugins
```

Search for **Within** and install the plugin.

The first request that hits a Within tool will prompt you to sign in.

## Install — Gemini CLI

```bash
gemini extensions install https://github.com/Within-AI/within-mcp
```

To login:
```
/mcp auth within
```

## Install — Microsoft Copilot

> (NOTE: there's no official Within connector for Microsoft Copilot yet. Your organization needs Copilot Studio to create and publish a custom agent that uses Within.)

Copilot Studio -> New agent -> Tools -> Add tool -> Add new MCP -> `https://api.within.ai/mcp` (OAuth 2.0, Dynamic discovery) -> Publish

Full walkthrough: <https://developers.within.ai/install/microsoft-copilot>

## Authentication
You will need to be a Within customer to access this app.

The first time the plugin connects, your AI client will prompt you to sign in
to Within. the MCP will use whatever authentication is configured by your organization for Within.

> **Fallback:** If your client does not yet support MCP OAuth, you can issue
> a personal API key from your Within workspace settings and configure your
> client to send it as a `Bearer` token. Contact
> [hello@within.ai](mailto:hello@within.ai) for guidance.

## Repository layout

| Path | What it is |
|---|---|
| `within_mcp/` | Python package: metadata + builders + CLI for regenerating manifests |
| `.claude-plugin/plugin.json` | Claude Code plugin manifest (generated) |
| `.claude-plugin/marketplace.json` | Claude Code marketplace catalog (generated) |
| `.codex-plugin/plugin.json` | Codex plugin manifest (generated) |
| `.agents/plugins/marketplace.json` | Codex marketplace catalog (generated) |
| `.mcp.json` | Shared MCP server config (HTTP transport; used by Claude Code and Codex) |
| `gemini-extension.json` | Gemini CLI extension manifest (generated) |
| `skills/within-process-context-graph/SKILL.md` | The Within skill prompt |
| `tests/test_packaging.py` | Manifest invariants + drift checks |
| `LICENSE` | Apache-2.0 (covers this shim only — see `NOTICE`) |
| `NOTICE` | Trademark + commercial-service notice |

## For Within Team - Regenerating Manifests

```bash
pip install -e ".[dev]"
python -m within_mcp --write     # regenerates the public manifests
python -m within_mcp --check     # CI-side drift check
pytest -v
```

The metadata that drives every manifest lives in
[`within_mcp/metadata.py`](./within_mcp/metadata.py). Edit there, then re-run
`--write` and commit the result.

## License

This plugin shim is released under the [Apache License 2.0](./LICENSE). The
Within service it connects to is a commercial service governed by
Within's Terms of Service. See [`NOTICE`](./NOTICE) for details.

## Links

- Within: <https://www.within.ai/>
- Privacy Policy: <https://www.within.ai/product-privacy-policy>
- Terms of Service: <https://www.within.ai/terms-of-service-2025>
- Issues / contact: <mailto:hello@within.ai>
