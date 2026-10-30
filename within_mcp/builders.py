"""Public manifest builders for Within MCP plugin distribution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from within_mcp.metadata import (
    CLAUDE_MARKETPLACE_PATH,
    CLAUDE_MCP_CONFIG_PATH,
    CLAUDE_PLUGIN_DIR,
    CODEX_MARKETPLACE_PATH,
    CODEX_PLUGIN_DIR,
    GEMINI_EXTENSION_PATH,
    WITHIN_MCP_METADATA,
    WithinMCPMetadata,
)


def build_claude_plugin_manifest(
    metadata: WithinMCPMetadata = WITHIN_MCP_METADATA,
) -> dict[str, Any]:
    """Output: .claude-plugin/plugin.json (Claude Code plugin manifest)."""
    return {
        "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
        "name": metadata.plugin_name,
        "version": metadata.plugin_version,
        "description": metadata.plugin_description,
        "author": {
            "name": metadata.author_name,
            "email": metadata.author_email,
            "url": metadata.author_url,
        },
        "homepage": metadata.homepage_url,
        "repository": metadata.repository_url,
        "license": metadata.license_spdx,
        "keywords": list(metadata.keywords),
        "mcpServers": "./.mcp.json",
    }


def build_claude_marketplace_manifest(
    metadata: WithinMCPMetadata = WITHIN_MCP_METADATA,
) -> dict[str, Any]:
    """Output: .claude-plugin/marketplace.json (Claude Code marketplace catalog).

    Required so `/plugin install <plugin>@<owner>/<repo>` resolves this repo as a
    marketplace. Without this file, Claude Code returns
    "Marketplace '<owner>/<repo>' not found".
    """
    return {
        "name": metadata.plugin_name,
        "owner": {
            "name": metadata.author_name,
            "email": metadata.author_email,
            "url": metadata.author_url,
        },
        "metadata": {
            "description": metadata.plugin_description,
            "version": metadata.plugin_version,
        },
        "plugins": [
            {
                "name": metadata.plugin_name,
                # Use the explicit `url` object form, not a relative-path string.
                # Direct `/plugin marketplace add owner/repo` flows don't accept
                # bare "." or "./" — Claude Code reports "source type your
                # Claude Code version does not support". The relative-path
                # string form works only after Anthropic's curation pipeline
                # transforms it into this same `url` form. Confirmed by
                # diffing vendor-authored marketplaces (e.g. sentry-for-claude
                # uses "./") against their ingested entry in
                # anthropics/claude-plugins-official (rewritten to `url`).
                "source": {
                    "source": "url",
                    "url": f"{metadata.repository_url}.git",
                },
                "description": metadata.plugin_description,
                "version": metadata.plugin_version,
                "homepage": metadata.homepage_url,
                "license": metadata.license_spdx,
                "keywords": list(metadata.keywords),
                "author": {
                    "name": metadata.author_name,
                    "email": metadata.author_email,
                    "url": metadata.author_url,
                },
            }
        ],
    }


def build_claude_mcp_config(
    metadata: WithinMCPMetadata = WITHIN_MCP_METADATA,
) -> dict[str, Any]:
    """Output: .mcp.json at the repo root (Claude Code MCP server config, HTTP transport).

    Lives at the plugin root (not under `.claude-plugin/`) because Claude's plugin
    loader resolves `mcpServers: "./.mcp.json"` from the plugin root.
    """
    return {
        "mcpServers": {
            metadata.mcp_server_key: {
                "type": "http",
                "url": metadata.mcp_url,
            }
        }
    }


def build_codex_plugin_manifest(
    metadata: WithinMCPMetadata = WITHIN_MCP_METADATA,
) -> dict[str, Any]:
    """Output: .codex-plugin/plugin.json (Codex plugin manifest).

    Mirrors Claude's `mcpServers: "./.mcp.json"` pointer so a single shared
    `.mcp.json` at the repo root serves both clients. The `interface` block
    drives the in-CLI presentation Codex renders during install.
    """
    return {
        "name": metadata.plugin_name,
        "version": metadata.plugin_version,
        "description": metadata.plugin_description,
        "author": {
            "name": metadata.author_name,
            "email": metadata.author_email,
            "url": metadata.author_url,
        },
        "homepage": metadata.homepage_url,
        "repository": metadata.repository_url,
        "license": metadata.license_spdx,
        "keywords": list(metadata.keywords),
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "interface": {
            "displayName": metadata.app_display_name,
            "shortDescription": metadata.interface_short_description,
            "longDescription": metadata.interface_long_description,
            "developerName": metadata.author_name,
            "category": metadata.category,
            "capabilities": list(metadata.capabilities),
            "websiteURL": metadata.homepage_url,
            "privacyPolicyURL": metadata.privacy_policy_url,
            "termsOfServiceURL": metadata.terms_of_service_url,
            "defaultPrompt": list(metadata.default_prompts),
            "brandColor": metadata.brand_color,
        },
    }


def build_codex_marketplace_manifest(
    metadata: WithinMCPMetadata = WITHIN_MCP_METADATA,
) -> dict[str, Any]:
    """Output: .agents/plugins/marketplace.json (Codex marketplace catalog).

    Codex's marketplace loader probes `.agents/plugins/marketplace.json` first
    (`codex-rs/core-plugins/src/marketplace.rs`). Source paths in this manifest
    must stay inside the marketplace root, so we can't `..` up to the
    `.codex-plugin/plugin.json` at the repo root with a local source. The
    cleanest "repo IS the plugin" mapping is a remote `url` source pointing
    back at the same repository — Codex clones it and finds the plugin
    manifest at the root.
    """
    return {
        "name": metadata.plugin_name,
        "interface": {
            "displayName": metadata.app_display_name,
        },
        "plugins": [
            {
                "name": metadata.plugin_name,
                "source": {
                    "source": "url",
                    "url": f"{metadata.repository_url}.git",
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": metadata.category,
            }
        ],
    }


def build_gemini_extension_manifest(
    metadata: WithinMCPMetadata = WITHIN_MCP_METADATA,
) -> dict[str, Any]:
    """Output: gemini-extension.json (Gemini CLI extension manifest).

    Uses the consolidated `url` key (not `httpUrl`) per Gemini CLI PR #13762.

    Gemini accepts a single `contextFileName`, selected by `metadata.gemini_context_skill`.
    """
    return {
        "name": metadata.plugin_name,
        "version": metadata.plugin_version,
        "description": metadata.plugin_description,
        "mcpServers": {
            metadata.mcp_server_key: {
                "url": metadata.mcp_url,
            }
        },
        "contextFileName": f"skills/{metadata.gemini_context_skill}/SKILL.md",
    }


def build_manifest_payloads(
    metadata: WithinMCPMetadata = WITHIN_MCP_METADATA,
) -> dict[Path, dict[str, Any]]:
    return {
        CLAUDE_PLUGIN_DIR / "plugin.json": build_claude_plugin_manifest(metadata),
        CLAUDE_MARKETPLACE_PATH: build_claude_marketplace_manifest(metadata),
        CLAUDE_MCP_CONFIG_PATH: build_claude_mcp_config(metadata),
        CODEX_PLUGIN_DIR / "plugin.json": build_codex_plugin_manifest(metadata),
        CODEX_MARKETPLACE_PATH: build_codex_marketplace_manifest(metadata),
        GEMINI_EXTENSION_PATH: build_gemini_extension_manifest(metadata),
    }


def render_manifest(payload: dict[str, Any]) -> str:
    """Serialize a manifest payload to JSON text. Trailing newline is significant."""
    return json.dumps(payload, indent=2, ensure_ascii=True) + "\n"


def build_manifest_texts(
    metadata: WithinMCPMetadata = WITHIN_MCP_METADATA,
) -> dict[Path, str]:
    return {
        path: render_manifest(payload)
        for path, payload in build_manifest_payloads(metadata).items()
    }
