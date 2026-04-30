#!/usr/bin/env bash
# Convert a Claude Code stream-json transcript (.jsonl) into a markdown chat log.
# Usage: ./transcript_to_markdown.sh <transcript.jsonl>
# Example: ./transcript_to_markdown.sh data/<run>.jsonl | glow -

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $(basename "$0") <transcript.jsonl>" >&2
  exit 2
fi

jq -r '
  def block:
    if .type == "text" then
      .text
    elif .type == "tool_use" then
      "**Tool: " + .name + "**\n\n```json\n" + (.input | tojson) + "\n```"
    elif .type == "tool_result" then
      "**Tool result**\n\n```\n" +
      (if (.content | type) == "array"
         then [.content[] | .text // ""] | join("\n")
         else (.content | tostring)
       end) +
      "\n```"
    else
      empty
    end;
  select(.type == "assistant" or .type == "user") |
  (if .type == "assistant" then "## Assistant" else "## User" end) +
  "\n\n" +
  ([.message.content[] | block] | join("\n\n")) +
  "\n\n---\n"
' "$1"
