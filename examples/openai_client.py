"""Adopting NanoGate = changing base_url and api_key. Everything else is the stock OpenAI SDK.

    export NANOGATE_API_KEY=...   # e.g. keys["app:acme/it"]["key"] from var/dev_keys.json
    python examples/openai_client.py
"""
import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ.get("NANOGATE_BASE_URL", "http://127.0.0.1:8080/v1"),
    api_key=os.environ["NANOGATE_API_KEY"],
)

# Non-streaming — decision metadata travels in response headers, never in the answer text.
raw = client.chat.completions.with_raw_response.create(
    model="nanogate-auto",
    messages=[{"role": "user", "content": "How do I reset the VPN client?"}],
)
resp = raw.parse()
print(resp.choices[0].message.content)
print("route:", raw.headers["x-nanogate-route"], "| reason:", raw.headers["x-nanogate-reason"],
      "| receipt:", raw.headers["x-nanogate-receipt-id"], "| tokens:", resp.usage.total_tokens)

# Streaming — real token stream from the local model.
stream = client.chat.completions.create(
    model="nanogate-auto", stream=True,
    messages=[{"role": "user", "content": "Give me three tips for a stable VPN connection."}],
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
print()
