"""Improvement loop: apply the triage recommendation for Q007 (show rep_name, not sales_rep_id) as a Genie
text instruction, preserving everything else in serialized_space. Re-evaluate only the affected core afterwards:
  uv run ... python 06_custom_harness.py --core-ids Q007 --run-name harness-Q007-after-fix
"""
import json

from common import load_state, w

FIX = ("When reporting metrics per sales rep, join rep_territory on sales_rep_id and return rep_name "
       "(not sales_rep_id) as the rep column, one row per rep.\n")

space_id = load_state()["space_id"]
payload = json.loads(w.genie.get_space(space_id, include_serialized_space=True).serialized_space)
content = payload["instructions"]["text_instructions"][0]["content"]
if FIX not in content:
    content.append(FIX)
    w.genie.update_space(space_id, serialized_space=json.dumps(payload))
after = json.loads(w.genie.get_space(space_id, include_serialized_space=True).serialized_space)
print("instruction lines:", len(after["instructions"]["text_instructions"][0]["content"]),
      "| benchmarks preserved:", len(after["benchmarks"]["questions"]))
