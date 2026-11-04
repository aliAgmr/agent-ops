#!/usr/bin/env python3
"""Stand-in for `claude -p ... --output-format json` in the offline agent-mcp tests.

Echoes how it was called (session flag, depth flags, the question) as the result, so tests can
check what the server passed. Special words in the question:
  PLANT  -> also print the value of FAKE_PLANTED_SECRET (redaction test)
  SLOW   -> sleep 1.5 s and append start/end times and session to FAKE_CLAUDE_TIMES (serialization test)
  FAIL   -> exit 1 with an error result
"""
import json
import os
import sys
import time

args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
session = None
mode = None
for flag in ("--session-id", "--resume"):
    if flag in args:
        session, mode = args[args.index(flag) + 1], flag
question = prompt.split("depth: ", 1)[-1]
start = time.time()
if "SLOW" in question:
    time.sleep(1.5)
    with open(os.environ["FAKE_CLAUDE_TIMES"], "a") as f:
        f.write(f"{start} {time.time()} {session}\n")
if "FAIL" in question:
    print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": session}))
    sys.exit(1)
lines = [f"mode={mode} session={session}",
         f"model={args[args.index('--model') + 1]} effort={args[args.index('--effort') + 1]} max_turns={args[args.index('--max-turns') + 1]}",
         f"cwd={os.getcwd()}",
         f"mcp_timeout={os.environ.get('MCP_TIMEOUT')}",
         f"question={question.strip()}"]
if "PLANT" in question:
    lines.append(f"the secret is {os.environ.get('FAKE_PLANTED_SECRET')} and pg password is {os.environ.get('FAKE_PG_PASSWORD')}")
text = "\n".join(lines) + "\n\n## Sources\n- fake/path.ts:1\n- SQL: select 1\n"
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                  "session_id": session, "num_turns": 1}))
