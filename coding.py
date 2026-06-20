"""Coding-turn intent detection.

When the user has set a codebase path and enabled coding mode (see ``Config``),
a turn that sounds like a coding or file-editing request is run by Claude Code
*inside* that codebase folder, as its own conversation (see
``claude_client.ClaudeClient.ask`` ``coding_cwd``). This module holds the cheap,
deterministic phrase match that decides "is this a coding turn?" — kept pure and
dependency-free so it imports and unit-tests anywhere, the same way
``browser_mcp`` keeps its intent detection separate from the heavy browser bits.

Detection is a substring match against the lowered transcript, so a normal turn
pays no latency and never reaches the model for routing. It only ever matters
when coding mode is switched on, so the bar for matching can be fairly generous
without disturbing plain voice use.
"""
from __future__ import annotations

import re

# Phrases that mark a turn as a coding / file-editing request. Matched as
# substrings against the lowered transcript. Bias toward an editing verb paired
# with a code/file noun so ordinary chat ("open the window", "fix my schedule")
# does not trip it.
_CODING_TRIGGERS = (
    # edit / change / modify a file or code
    "edit the",
    "edit my",
    "edit this file",
    "edit that file",
    "modify the",
    "change the function",
    "change the method",
    "change the class",
    "change the code",
    "change the file",
    "update the function",
    "update the method",
    "update the code",
    "update the file",
    "tweak the",
    # fix / refactor
    "fix the bug",
    "fix the code",
    "fix this code",
    "fix the function",
    "fix the error in",
    "fix the failing",
    "refactor",
    "clean up the code",
    # add / write / create code
    "add a function",
    "add a method",
    "add a class",
    "add a parameter",
    "add a field",
    "write a function",
    "write the function",
    "write a class",
    "write a script",
    "implement the",
    "implement a",
    "create a file",
    "create a new file",
    "create a function",
    "make a new file",
    # rename / delete / comment
    "rename the",
    "delete the function",
    "delete the file",
    "remove the function",
    "comment out",
    "uncomment",
    # file kinds (e.g. "edit the cpp file", "open the python file")
    "the cpp file",
    "the c plus plus file",
    "the header file",
    "the source file",
    "the python file",
    "the javascript file",
    "the typescript file",
    "the rust file",
    "the go file",
    "the java file",
    "the config file",
    "dot cpp",
    "dot py",
    "dot js",
    "dot ts",
    # project-scoped phrasings ("in the repo" is matched as a whole word below,
    # so it can't fire inside an unrelated longer word like "in the report").
    "in the codebase",
    "in the repository",
    "in the project",
    "the codebase",
    # build / version control actions on the project
    "run the build",
    "git commit",
    "commit the change",
    "commit the changes",
    "push to main",
)

# Short, abbreviation-style cues that must match as whole words, so the casual
# "in the repo(s)" is caught but everyday speech like "in the report" is not.
# Kept apart from the plain substring triggers above (which stay a cheap "in"
# test); longer triggers like "refactor" intentionally still match as substrings
# so "refactoring" trips them too.
_CODING_WORD_TRIGGER_RE = re.compile(r"\bin the repos?\b")


def looks_like_coding_request(text: str) -> bool:
    """True if the transcript clearly asks for a coding / file-editing action."""
    lowered = (text or "").lower()
    if any(trigger in lowered for trigger in _CODING_TRIGGERS):
        return True
    return _CODING_WORD_TRIGGER_RE.search(lowered) is not None
