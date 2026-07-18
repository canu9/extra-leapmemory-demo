import os

import requests


def recall_customer(input: dict) -> str:
    """Look up what we know about this customer. Pass the customer's question as 'query'."""
    r = requests.post(
        f"https://api.leapmemory.com/v1/tenants/{os.environ['LM_TENANT']}/recall",
        headers={"Authorization": f"Bearer {os.environ['LM_API_KEY']}"},
        json={"query": input["query"]},
        timeout=15,
    )
    data = r.json().get("data", {})
    facts = [f["sentence"] for f in data.get("facts", [])]
    chunks = [c["content"] for c in data.get("chunks", [])[:2]]
    if not facts and not chunks:
        return "No customer memory found for that."
    return "Known about this customer: " + " ".join(facts + chunks)