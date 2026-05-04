from __future__ import annotations
import asyncio
import os
from pathlib import Path
import typer
from rich.console import Console
from rich.markdown import Markdown
from kc_core.agent import Agent
from kc_core.config import load_agent_config
from kc_core.ollama_client import OllamaClient
from kc_core.tools import ToolRegistry


app = typer.Typer(help="kc-chat — interactive chat with a kc-core agent.")
console = Console()


@app.command()
def main(
    agent: Path = typer.Option(..., "--agent", "-a", exists=True, help="Path to agent YAML config"),
    base_url: str = typer.Option("http://localhost:11434", "--ollama-url", help="OpenAI-compatible base URL (Ollama or e.g. https://openrouter.ai/api/v1)"),
    default_model: str = typer.Option("gemma3:4b", "--default-model", help="Model to use if the agent YAML doesn't specify one"),
    api_key: str = typer.Option(None, "--api-key", envvar="OPENROUTER_API_KEY", help="API key for OpenRouter or other paid providers; defaults to OPENROUTER_API_KEY env var. Ollama does not need one."),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Stream tokens as the model generates them"),
) -> None:
    """Start an interactive chat with the agent defined by AGENT yaml file."""
    cfg = load_agent_config(agent, default_model=default_model)
    client = OllamaClient(base_url=base_url, model=cfg.model, api_key=api_key or None)
    agent_obj = Agent(
        name=cfg.name,
        client=client,
        system_prompt=cfg.system_prompt,
        tools=ToolRegistry(),
    )
    provider_label = "OpenRouter" if api_key else "Ollama"
    console.print(f"[bold cyan]kc-chat[/] connected to [bold]{cfg.model}[/] via [bold]{provider_label}[/] as [bold]{cfg.name}[/]")
    console.print("[dim]Type a message and press Enter. Ctrl-C to quit.[/]\n")

    async def _run() -> None:
        while True:
            try:
                user = console.input("[bold green]you>[/] ")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye.[/]")
                return
            if not user.strip():
                continue

            console.print(f"[bold magenta]{cfg.name}>[/] ", end="")
            if stream:
                from kc_core.messages import UserMessage, AssistantMessage
                agent_obj.history.append(UserMessage(content=user))
                buf = ""
                wire = agent_obj._build_wire_messages()
                async for delta in client.chat_stream(messages=wire, tools=[]):
                    console.print(delta, end="")
                    buf += delta
                console.print()
                agent_obj.history.append(AssistantMessage(content=buf))
            else:
                reply = await agent_obj.send(user)
                console.print(Markdown(reply.content))

    asyncio.run(_run())


if __name__ == "__main__":
    app()
