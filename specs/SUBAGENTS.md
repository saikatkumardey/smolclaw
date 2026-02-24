# Sub-Agents Spec

How smolclaw would support sub-agents without violating the philosophy.

## When sub-agents make sense

The single agent handles 95% of tasks. Sub-agents are for:
- Long tasks that block the main conversation (research while the user keeps chatting)
- Isolated work that shouldn't pollute the main agent's context (data processing, code generation)
- Parallel execution (do 3 things at once)

If the user didn't ask for parallelism or isolation, the single agent handles it. No auto-spawning.

## How smolagents supports this

smolagents has `ManagedAgent` — wraps an agent as a callable tool. The orchestrator agent can call it like any other tool:

```python
from smolagents import ToolCallingAgent, ManagedAgent, LiteLLMModel

# Create a worker agent
worker = ToolCallingAgent(
    tools=[shell_exec, file_read, file_write, python_interpreter],
    model=LiteLLMModel(model_id=MODEL),
    max_steps=15,
)

# Wrap it as a managed agent (appears as a tool to the orchestrator)
managed = ManagedAgent(
    agent=worker,
    name="worker",
    description="Handles isolated tasks like research, file processing, or code generation. Give it a clear task description.",
)

# Orchestrator gets the managed agent
orchestrator = ToolCallingAgent(
    tools=[...all regular tools...],
    model=LiteLLMModel(model_id=MODEL),
    managed_agents=[managed],
)
```

When the orchestrator calls the worker, it sends a task string and gets back a result string. The worker runs in its own context — no shared memory with the orchestrator.

## Design

### Option A: Always-on worker (simple)

Create one managed worker agent at startup. The orchestrator can delegate to it anytime.

```
User → Orchestrator Agent (main) → [tools + worker agent]
                                         ↓
                                   Worker Agent (isolated)
                                   [shell, files, python, web]
```

Pros: zero user config, smolagents handles everything
Cons: worker uses the same model (cost), always loaded even if never used

### Option B: On-demand via tool (flexible)

Add a `spawn_task` tool that creates a temporary agent, runs the task, returns the result, and disposes. No persistent worker.

```python
class SpawnTaskTool(Tool):
    name = "spawn_task"
    description = "Run an isolated task in a separate agent. Use for long or independent work."
    inputs = {
        "task": {"type": "string", "description": "What the sub-agent should do"},
        "tools": {"type": "string", "description": "Comma-separated tool names to give the sub-agent. Default: shell_exec,file_read,file_write,python_interpreter,visit_webpage,web_search"},
    }
    output_type = "string"

    def forward(self, task: str, tools: str = "") -> str:
        tool_names = [t.strip() for t in tools.split(",")] if tools else DEFAULT_WORKER_TOOLS
        worker_tools = [t for t in TOOLS_LIST if t.name in tool_names]
        worker = ToolCallingAgent(
            tools=worker_tools,
            model=LiteLLMModel(model_id=MODEL),
            max_steps=15,
        )
        result = worker.run(task)
        return str(result)
```

Pros: only created when needed, tool selection per task, no overhead
Cons: no persistent memory across sub-agent calls, each spawn is a cold start

### Recommendation: Option B

Aligns with smolclaw philosophy:
- "One agent is enough" — sub-agents are opt-in, not default
- "Small is the point" — one tool class, ~20 lines of code
- "Load what you need" — only created when the orchestrator decides to delegate

## What the orchestrator sees

The orchestrator's SOUL.md gets a new section:

```markdown
### Sub-agents

I can spawn isolated sub-agents for tasks that would take too long or clutter my context.
I use `spawn_task(task="...", tools="...")` to delegate work.
The sub-agent runs independently and returns a result.

I use this when:
- A task involves many steps that don't need my conversation context
- I want to process data without filling my context with intermediate output
- The user asks me to do multiple things at once

I don't use this for:
- Simple questions or quick tool calls
- Anything that needs my conversation history
- Tasks that require user interaction mid-way
```

## Constraints

- **Max steps:** 15 per sub-agent (prevent runaway loops)
- **Timeout:** 120 seconds (kill if stuck)
- **No telegram_send:** sub-agents cannot message the user directly — only the orchestrator does
- **No self_restart/self_update:** dangerous tools stay with the orchestrator
- **No spawning sub-agents from sub-agents:** one level deep only

## Session logging

Sub-agent work gets logged to the same daily JSONL file:
```json
{"ts": "...", "chat_id": "123", "role": "system", "content": "SUBAGENT_START: task description"}
{"ts": "...", "chat_id": "123", "role": "system", "content": "SUBAGENT_RESULT: result text"}
```

## Implementation plan

1. Add `SpawnTaskTool` to `tools.py` (~30 lines)
2. Add it to `TOOLS_LIST`
3. Add sub-agent section to `SOUL.md` template
4. Add timeout wrapper (threading.Timer or signal.alarm)
5. Add session logging for sub-agent start/result
6. Test: mock LiteLLMModel, verify spawn_task returns a string

Estimated: ~50 lines of new code. No new dependencies.

## What we're NOT building

- Multi-agent orchestration frameworks
- Persistent specialist agents (like OpenClaw's Coach/Editor/Ops)
- Agent-to-agent communication
- Shared memory between agents
- Background/async sub-agents (everything is synchronous — the orchestrator waits)

If any of those become needed, we revisit. Until then, one tool, one level, synchronous.
