"""CAM audit as a Fetch.ai uAgent. It speaks the standard chat protocol, so it
can be found and messaged from Agentverse and ASI:One.

    AGENTVERSE_API_KEY=... CAM_AGENT_SEED=... python -m cam.fetch_agent

Chat commands:
    audit                     run the sample audit and reply with the result
    audit L9=exclude_capital  same, with the owner's answer to an open question
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import aiohttp
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, EndSessionContent, TextContent, chat_protocol_spec,
)

from . import run as cam_run

PORT = int(os.environ.get("CAM_AGENT_PORT", "8001"))
README = Path(__file__).with_name("AGENT_README.md")

cam_run.load_env()
if os.environ.get("AGENTVERSE_API_KEY") and not os.environ.get("CAM_AGENT_SEED"):
    raise SystemExit("Set CAM_AGENT_SEED (a long random phrase) before connecting to Agentverse: it is the agent's key.")
agent = Agent(name="cam-audit", seed=os.environ.get("CAM_AGENT_SEED", "cam-audit local dev seed"),
              port=PORT, mailbox=True, description="Audits CAM reconciliations against the lease",
              readme_path=str(README), publish_agent_details=True)
chat = Protocol(spec=chat_protocol_spec)


def reply(text: str, end: bool = True) -> ChatMessage:
    content = [TextContent(type="text", text=text)] + ([EndSessionContent(type="end-session")] if end else [])
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content)


def answer(text: str) -> str:
    words = text.split()
    if not words or words[0].lower() != "audit":
        return ("Send `audit` to run the sample CAM audit, or `audit L9=exclude_capital` to answer "
                "its open question about the roof line.")
    rulings = dict(w.split("=", 1) for w in words[1:] if "=" in w)
    reader = "claude" if os.environ.get("ANTHROPIC_API_KEY") else "regex"
    r = cam_run.run(cam_run.SAMPLE / "lease.md", cam_run.SAMPLE / "statement.yaml", "428-0061-012-03", "Alameda",
                    reader, "claude" if reader == "claude" else "template", rulings, None,
                    cam_run.SAMPLE / "assessor_cache.json")
    return cam_run.render(r)


@chat.on_message(ChatMessage)
async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
    text = " ".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
    ctx.logger.info(f"chat from {sender}: {text!r}")
    try:
        out = await asyncio.to_thread(answer, text)
    except Exception as e:  # report, don't drop the conversation
        out = f"The audit failed: {e}"
    await ctx.send(sender, reply(out))


@chat.on_message(ChatAcknowledgement)
async def on_ack(_ctx: Context, _sender: str, _msg: ChatAcknowledgement):
    pass


agent.include(chat, publish_manifest=True)


@agent.on_event("startup")
async def connect_mailbox(ctx: Context):
    """Connect this agent's mailbox on Agentverse with the API key, the same call
    the Agentverse inspector makes when you press Connect."""
    key = os.environ.get("AGENTVERSE_API_KEY")
    if not key:
        ctx.logger.warning("AGENTVERSE_API_KEY not set; running without an Agentverse mailbox")
        return

    async def go():
        await asyncio.sleep(2)  # let the local REST server come up
        async with aiohttp.ClientSession() as s:
            async with s.post(f"http://127.0.0.1:{PORT}/connect",
                              json={"user_token": key, "agent_type": "mailbox"}) as resp:
                ctx.logger.info(f"Agentverse connect: {resp.status} {await resp.text()}")
    asyncio.create_task(go())


if __name__ == "__main__":
    print(f"CAM audit agent address: {agent.address}")
    agent.run()
