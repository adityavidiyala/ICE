"""
context/manager.py — Lightweight context window manager for ICE.

Tracks token usage reported by the Gemini API after each turn and prunes
stale tool outputs from the chat history when the window fills up.

Pruning strategy (adapted from Postal):
  - Walk the history oldest-to-newest
  - Identify all "tool result" user-messages (function_response parts)
  - Protect the N most recent ones — replace the rest with a short placeholder
  - Only prune an output if it is long enough to be worth clearing
  - After pruning, rebuild the chat session with the trimmed history
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass   # avoid circular imports at definition time

# ── Thresholds ─────────────────────────────────────────────────────────────────

# Trigger pruning when the API reports this many total tokens in-flight.
# Gemini 2.5 Flash has a ~1 M token context window; we prune well before that.
PRUNE_THRESHOLD: int = 80_000

# Keep this many trailing tool-output user-messages safe from pruning.
# This protects recent context the model still needs to reason about.
PROTECT_RECENT_OUTPUTS: int = 6

# Skip pruning a tool output unless its serialised text is at least this long.
# Small outputs aren't worth replacing (they save almost no tokens).
MIN_CHARS_TO_PRUNE: int = 300

# What we replace pruned tool output with.
PRUNED_PLACEHOLDER: str = "[Tool output cleared — context pruned to save token budget]"


# ── Manager ────────────────────────────────────────────────────────────────────

class ContextManager:
    """Tracks token usage and prunes stale tool outputs from a Gemini chat history."""

    def __init__(self) -> None:
        self._total_tokens: int = 0
        self._pruned_total: int = 0   # cumulative count across the whole session

    # -- Token accounting ------------------------------------------------------

    def record_token_usage(self, total_tokens: int) -> None:
        """Call this after every API response with usage_metadata.total_token_count."""
        self._total_tokens = total_tokens

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def pruned_total(self) -> int:
        return self._pruned_total

    def needs_pruning(self) -> bool:
        return self._total_tokens > PRUNE_THRESHOLD

    # -- History pruning -------------------------------------------------------

    def prune_history(self, history: list) -> tuple[list, int]:
        """Return history with old tool outputs replaced by a placeholder.

        Args:
            history: The list of ``types.Content`` objects from ``chat_session.get_history()``.

        Returns:
            (new_history, n_pruned) where n_pruned is the number of tool
            outputs that were replaced.
        """
        from google.genai import types  # local import — avoids polluting module namespace

        # Find indices of every user-message that carries a function_response
        tool_result_indices: list[int] = []
        for i, content in enumerate(history):
            if getattr(content, "role", None) == "user":
                for part in (content.parts or []):
                    if getattr(part, "function_response", None) is not None:
                        tool_result_indices.append(i)
                        break

        if not tool_result_indices:
            return list(history), 0

        # Everything except the most recent PROTECT_RECENT_OUTPUTS entries is eligible
        eligible = set(tool_result_indices[:-PROTECT_RECENT_OUTPUTS]) if len(tool_result_indices) > PROTECT_RECENT_OUTPUTS else set()

        if not eligible:
            return list(history), 0

        pruned_count = 0
        new_history: list = []

        for i, content in enumerate(history):
            if i not in eligible:
                new_history.append(content)
                continue

            # Rebuild this Content's parts, replacing large function_response payloads
            new_parts = []
            replaced_any = False

            for part in (content.parts or []):
                fn_resp = getattr(part, "function_response", None)
                if fn_resp is not None:
                    # Serialise current response to measure size
                    resp_text = str(getattr(fn_resp, "response", "") or "")
                    if len(resp_text) >= MIN_CHARS_TO_PRUNE:
                        # Replace with lightweight placeholder
                        new_parts.append(
                            part.model_copy(update={"function_response":
                                fn_resp.model_copy(update={
                                    "response": {"result": PRUNED_PLACEHOLDER}
                                })
                            })
                        )
                        replaced_any = True
                    else:
                        new_parts.append(part)
                else:
                    new_parts.append(part)

            new_history.append(content.model_copy(update={"parts": new_parts}))
            if replaced_any:
                pruned_count += 1

        self._pruned_total += pruned_count
        return new_history, pruned_count
