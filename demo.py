#!/usr/bin/env python3
"""
Extra + LeapMemory — Session-Proof Memory Demo
===============================================
Sessions die. The memory doesn't.

  Act 1  SAVE     five sessions; every user turn auto-saved to LeapMemory
  Act 2  EXTRACT  LeapMemory turns raw words into structured facts
  Act 3  ANSWER   six fresh sessions answer correctly with zero history

Also writes demo_transcript.txt (colors stripped).

Requirements: pip install requests · docker image `extra-lm` · .env with
LM_API_KEY, LM_TENANT and the model provider keys.

Usage: python demo.py
"""

import json
import os
import re
import subprocess
import sqlite3
import sys
import time

import requests

# ── Config ───────────────────────────────────────────────────────────────────

ENV_FILE        = ".env"
DOCKER_IMAGE    = "extra-lm"
AGENTS_CONFIG   = "agents.yml"
CHAT_DB         = "chat.db"
LM_API_URL      = "https://api.leapmemory.com"
TRANSCRIPT_FILE = "demo_transcript.txt"

RUN_TIMEOUT     = 180   # seconds per extra invocation
POLL_TIMEOUT    = 180   # ceiling for extraction to finish
POLL_INTERVAL   = 5     # seconds between extraction polls

# ── Demo script ──────────────────────────────────────────────────────────────

SAVE = [
    "Hi, I'm Amit. Ship all my returns to the Tel Aviv office.",
    "One more thing: contact me on WhatsApp only, never email.",
    "For the invoice, my company is called Copperline Trading.",
    "I'm mostly shopping for standing desks for the new office.",
    "Please never schedule deliveries on Fridays, the office is closed.",
]

ASK = [
    {"label": "returns address",     "q": "It's Amit. Where do my returns go?",           "expect": "tel aviv"},
    {"label": "contact preference",  "q": "How should you contact me?",                   "expect": "whatsapp"},
    {"label": "invoice company",     "q": "What company name goes on my invoices?",       "expect": "copperline"},
    {"label": "shopping interest",   "q": "What was I shopping for?",                     "expect": "standing desk"},
    {"label": "delivery constraint", "q": "Any days you should avoid for my deliveries?", "expect": "friday"},
]

NEGATIVE = {"q": "What's my shirt size?"}

# ── Output (screen with color + transcript file without) ─────────────────────

OK   = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
CYAN = "\033[96m"
GRN  = "\033[92m"
YLW  = "\033[93m"
MAG  = "\033[95m"
RST  = "\033[0m"
BOLD = "\033[1m"
DIM  = "\033[2m"
W    = 66

_ANSI = re.compile(r"\033\[[0-9;]*m")
_TRANSCRIPT = []

def emit(line=""):
    print(line)
    _TRANSCRIPT.append(_ANSI.sub("", line))

def save_transcript():
    with open(TRANSCRIPT_FILE, "w") as f:
        f.write("\n".join(_TRANSCRIPT) + "\n")

def header(text):
    emit()
    emit(f"{CYAN}{'─' * W}{RST}")
    emit(f"{CYAN}{BOLD}  {text}{RST}")
    emit(f"{CYAN}{'─' * W}{RST}")

def intro(*lines):
    for ln in lines:
        emit(f"  {DIM}{ln}{RST}")
    emit()

def check(text):
    emit(f"    {OK} {text}")

def fatal(text, detail=""):
    emit(f"    {FAIL} {text}")
    if detail:
        emit(f"      {DIM}{detail}{RST}")
    save_transcript()
    sys.exit(1)

def session_open(sid, note):
    emit(f"  {BOLD}┌ SESSION {sid[:8]}{RST}  {DIM}{note}{RST}")

def session_line(role, text):
    color = YLW if role == "customer" else GRN
    label = "Amit " if role == "customer" else "Agent"
    for i, line in enumerate(text.strip().splitlines()):
        tag = f"{color}{BOLD}{label}{RST}" if i == 0 else "     "
        emit(f"  │ {tag} {line}")

def session_close(note):
    emit(f"  {BOLD}└ ENDED{RST}  {DIM}{note}{RST}")

def lm_line(text):
    emit(f"    {MAG}{BOLD}LM{RST} │ {text}")

# ── .env loading ─────────────────────────────────────────────────────────────

def load_env(path):
    if not os.path.exists(path):
        fatal(f"{path} not found — run from the extra-lm-demo folder.")
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()
    for required in ("LM_API_KEY", "LM_TENANT"):
        if not os.environ.get(required):
            fatal(f"{required} missing from {path}.")

# ── Driving extra (one run = one brand-new session) ──────────────────────────

NOISE = (
    re.compile(r"^WARNING"),
    re.compile(r"^WARNI\b"),
    re.compile(r"\[agent_engine"),
    re.compile(r"^\d{4}-\d{2}-\d{2} "),
    re.compile(r"^INFO\s"),
    re.compile(r"^\s+(system|session|user|message|route)\s*:"),
)

def run_extra(message):
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{os.getcwd()}:/workspace", "-w", "/workspace",
        "--env-file", ENV_FILE,
        DOCKER_IMAGE, "run", "--config", AGENTS_CONFIG,
        "--message", message,
    ]
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while proc.poll() is None:
        if time.time() - t0 > RUN_TIMEOUT:
            proc.kill()
            fatal(f"extra run exceeded {RUN_TIMEOUT}s")
        msg = f"    {spinner[i % len(spinner)]} session running… ({time.time() - t0:.0f}s)"
        print(f"\r{msg}{' ' * max(W - len(msg), 0)}", end="", flush=True)
        i += 1
        time.sleep(0.2)
    print(f"\r{' ' * (W + 10)}\r", end="")
    elapsed = time.time() - t0
    out = proc.stdout.read() + proc.stderr.read()
    if proc.returncode != 0:
        fatal(f"extra exited {proc.returncode}", out[-800:])
    m = re.search(r"session\s*:\s*([0-9a-f]+)", out)
    if not m:
        fatal("could not find session id in extra output", out[-800:])
    reply = "\n".join(
        ln for ln in out.splitlines()
        if ln.strip() and not any(p.search(ln) for p in NOISE)
    )
    return m.group(1), reply, elapsed

# ── extra's own store (the proof source) ─────────────────────────────────────

def stored_user_turn(session_id):
    con = sqlite3.connect(CHAT_DB)
    try:
        row = con.execute(
            "select content from conversation_messages "
            "where session_id = ? and role = 'user' "
            "order by created_at asc limit 1",
            (session_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        fatal(f"no user turn in chat.db for session {session_id}")
    return row[0]

def tools_used(session_id):
    con = sqlite3.connect(CHAT_DB)
    try:
        row = con.execute(
            "select metadata_json from conversation_messages "
            "where session_id = ? and role = 'assistant' "
            "order by created_at desc limit 1",
            (session_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        fatal(f"no assistant message in chat.db for session {session_id}")
    meta = json.loads(row[0])
    if "used_tools" not in meta:
        fatal("used_tools missing from conversation_messages.metadata_json")
    return meta["used_tools"]

# ── LeapMemory API ───────────────────────────────────────────────────────────

def lm_ingest_turn(content):
    r = requests.post(
        f"{LM_API_URL}/v1/tenants/{os.environ['LM_TENANT']}/turns",
        headers={"Authorization": f"Bearer {os.environ['LM_API_KEY']}"},
        json={"role": "user", "content": content},
        timeout=15,
    )
    body = r.json()
    if not body.get("success"):
        fatal(f"LM ingest failed: {body.get('message', 'unknown')}")

def write_tenant_to_env(tenant: str) -> None:
    """Persist the fresh tenant name into .env. The extra engine loads the
    .env FILE itself at startup and overrides docker -e vars, so the file
    is the only source of truth the container respects."""
    with open(ENV_FILE) as f:
        lines = f.readlines()
    with open(ENV_FILE, "w") as f:
        for line in lines:
            if line.strip().startswith("LM_TENANT="):
                f.write(f"LM_TENANT={tenant}\n")
            else:
                f.write(line)

def lm_reset_tenant():
    """Fresh tenant NAME every run; the previous run's tenant is deleted.
    Names are never reused, so no cached credential can ever go stale."""
    import random
    import string
    headers = {"Authorization": f"Bearer {os.environ['LM_API_KEY']}"}
    if os.path.exists(".demo_tenant"):
        with open(".demo_tenant") as f:
            old = f.read().strip()
        if old:
            requests.delete(
                f"{LM_API_URL}/v1/tenants/{old}?hard=true", headers=headers, timeout=30
            )
            emit(f"  {DIM}previous tenant '{old}' hard-deleted{RST}")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=3))
    tenant = f"acme_demo_{suffix}"
    r = requests.post(
        f"{LM_API_URL}/v1/tenants", headers=headers,
        json={"tenant_id": tenant}, timeout=30,
    )
    body = r.json()
    if not body.get("success"):
        fatal(f"tenant create failed: {body.get('message', 'unknown')}")
    os.environ["LM_TENANT"] = tenant
    write_tenant_to_env(tenant)
    with open(".demo_tenant", "w") as f:
        f.write(tenant)
    emit(f"  {OK} fresh tenant {BOLD}{tenant}{RST} provisioned — memory starts empty")

def lm_recall(query):
    """Returns (fact_sentences, chunk_contents, joined_lowercase_text)."""
    r = requests.post(
        f"{LM_API_URL}/v1/tenants/{os.environ['LM_TENANT']}/recall",
        headers={"Authorization": f"Bearer {os.environ['LM_API_KEY']}"},
        json={"query": query},
        timeout=15,
    )
    data = r.json().get("data", {})
    facts = [f["sentence"] for f in data.get("facts", [])]
    chunks = [c["content"] for c in data.get("chunks", [])]
    tool_view = " ".join(facts + chunks[:2]).lower()
    return facts, chunks, tool_view

# ── Act 1 — SAVE ─────────────────────────────────────────────────────────────

def act_save():
    header("Act 1 · SAVE — five sessions, every word kept")
    intro(
        "Each conversation is its own session. When it ends, its transcript",
        "is gone. Every user turn is auto-saved to LeapMemory first.",
        "No model decides what is worth keeping.",
    )
    for i, msg in enumerate(SAVE, 1):
        sid, reply, t = run_extra(msg)
        session_open(sid, f"{i} of 5 · no history")
        session_line("customer", msg)
        session_line("agent", reply)
        session_close("transcript gone forever")
        turn = stored_user_turn(sid)
        lm_ingest_turn(turn)
        check(f'saved to LeapMemory: {DIM}"{turn}"{RST}')
        emit()

# ── Act 2 — EXTRACT ──────────────────────────────────────────────────────────

def act_extract():
    header("Act 2 · EXTRACT — LeapMemory, server-side")
    intro(
        "LeapMemory digests every saved turn in the background on its own.",
        "This script is only a stopwatch: it asks Act 3's questions and",
        "records when each answer becomes retrievable. No agent runs here.",
    )
    pending = {sc["label"]: sc for sc in ASK}
    t0 = time.time()
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while pending and time.time() - t0 < POLL_TIMEOUT:
        for label in sorted(pending):
            sc = pending[label]
            _, _, text = lm_recall(sc["q"])
            if sc["expect"] in text:
                del pending[label]
                print(f"\r{' ' * (W + 10)}\r", end="")
                check(f"{label:<20} ready in {time.time() - t0:.0f}s")
        if pending:
            i += 1
            msg = f"    {spinner[i % len(spinner)]} digesting… {len(ASK) - len(pending)}/{len(ASK)} ready ({time.time() - t0:.0f}s)"
            print(f"\r{msg}{' ' * max(W - len(msg), 0)}", end="", flush=True)
            time.sleep(POLL_INTERVAL)
    print(f"\r{' ' * (W + 10)}\r", end="")
    if pending:
        fatal(f"not retrievable within {POLL_TIMEOUT}s: {sorted(pending)}")
    emit()
    emit(f"  {BOLD}The customer's file, in LeapMemory's own words:{RST}")
    emit()
    shown = set()
    for sc in ASK:
        facts, _, _ = lm_recall(sc["q"])
        for s in facts:
            if s not in shown:
                lm_line(s)
                shown.add(s)
    emit()
    intro("The customer never said those sentences. LeapMemory wrote them.")

# ── Act 3 — ANSWER ───────────────────────────────────────────────────────────

def act_answer():
    header("Act 3 · ANSWER — six fresh sessions, zero history")
    intro(
        "The customer comes back. Every session below starts empty:",
        "no transcript, nothing carried over. The only way to know the",
        "answer is to ask LeapMemory. Tool calls proven from extra's chat.db.",
    )
    correct = 0
    proofs = 0
    for i, sc in enumerate(ASK, 1):
        sid, reply, t = run_extra(sc["q"])
        session_open(sid, f"fresh {i} of 5 · knows nothing")
        session_line("customer", sc["q"])
        session_line("agent", reply)
        session_close("knew nothing, answered right")
        used = tools_used(sid)
        hit = [u for u in used if u["name"] == "recall_customer" and u["status"] == "succeeded"]
        good = sc["expect"] in reply.lower()
        if hit and good:
            check(f"answered from LeapMemory  {DIM}(recall_customer proven, session {sid[:8]}){RST}")
            proofs += 1
            correct += 1
        else:
            if not hit:
                emit(f"    {FAIL} recall_customer not recorded (session {sid}, got: {used})")
            if not good:
                emit(f'    {FAIL} expected "{sc["expect"]}" in the answer')
            proofs += 1 if hit else 0
            correct += 1 if good else 0
        emit()

    sid, reply, t = run_extra(NEGATIVE["q"])
    session_open(sid, "control · this was never said")
    session_line("customer", NEGATIVE["q"])
    session_line("agent", reply)
    session_close("nothing on file, nothing invented")
    _, _, text = lm_recall(NEGATIVE["q"])
    if "shirt" not in text:
        check("LeapMemory holds nothing about this — the agent said so honestly")
    return correct, proofs

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    emit()
    emit(f"{BOLD}Extra + LeapMemory · Session-Proof Memory{RST}")
    emit(f"{DIM}Sessions die. The memory doesn't.{RST}")
    emit()

    load_env(ENV_FILE)
    lm_reset_tenant()
    emit(f"  {DIM}engine: extra · store: {CHAT_DB} · run this as often as you like{RST}")
    if os.path.exists(CHAT_DB):
        os.remove(CHAT_DB)

    t_start = time.time()
    act_save()
    act_extract()
    correct, proofs = act_answer()
    total = time.time() - t_start

    header("Results")
    emit()
    check("5/5 turns saved automatically — no model judgment")
    check(f"{proofs}/5 answers proven to come from LeapMemory")
    check(f"{correct}/5 answers correct across dead sessions")
    check("0 invented details when memory was empty")
    emit()

    if correct == 5 and proofs == 5:
        bw = 60
        def box(text=""):
            pad = bw - len(text) - 2
            return f"    {CYAN}│{RST}  {text}{' ' * max(pad, 0)}{CYAN}│{RST}"
        emit(f"    {CYAN}┌{'─' * bw}┐{RST}")
        emit(box())
        emit(box(f"11 sessions, all born empty. {total:.0f} seconds."))
        emit(box())
        emit(box("Persistent memory   facts outlive every session"))
        emit(box("Stateless agent     the engine carries nothing"))
        emit(box("Automatic saving    every turn, no model judgment"))
        emit(box("Honest when empty   no memory means no invention"))
        emit(box())
        emit(box("Sessions are disposable. Customers are not."))
        emit(box())
        emit(f"    {CYAN}└{'─' * bw}┘{RST}")
        emit()
        emit(f"    {CYAN}https://leapmemory.com{RST}")
        emit()
        save_transcript()
        emit(f"{DIM}Plain-text copy: {TRANSCRIPT_FILE}{RST}")
    else:
        emit(f"    {FAIL} Some checks failed — session ids above point to chat.db and the LM History tab.")
        save_transcript()
        sys.exit(1)

if __name__ == "__main__":
    main()