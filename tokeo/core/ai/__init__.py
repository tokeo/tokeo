"""
AI provider core for Tokeo applications.

A small, dependency-light layer for talking to chat-completion LLMs. The
design mirrors the vault: named profiles live in the ```ai``` config section,
each profile selects a registered provider through its ```type```, and the
remaining keys are provider specific.

```yaml
ai:
  default: assistant
  profiles:
    assistant:
      type: openai
      options:
        model: qwen2.5
        base_url: http://localhost:11434/v1
      purpose: general
```

A provider is a dumb transport: given an already-resolved profile it turns a
list of messages into a normalized ```ChatResult```. Providers, tools and the
other services are registered as classes; the ```app.ai``` handler instantiates
them with the application. A ```type``` is either a short name from tokeo's
registry or a dotted ```module.Class``` path imported on demand; the registry is
reachable for inspection via ```app.ai.registry```. They keep no mutable per-call
state, so they are safe to use from several threads at once (for example
dramatiq workers or scheduler jobs).

### Notes

    : The local-first case points ```base_url``` at a server the user runs
        themselves (Ollama, llama.cpp, vLLM, MLX). Tokeo talks to that server
        but does not start or manage it.

"""

from tokeo.core.exc import TokeoError

# the package facade: the public names live in focused modules (data shapes,
# one base class per concern); import them from here as before
from tokeo.core.ai.data import Usage, ToolCall, ToolResult, ChatResult, Invocation
from tokeo.core.ai.provider import TokeoAiProvider
from tokeo.core.ai.tool import TokeoAiTool
from tokeo.core.ai.agent import TokeoAiAgent, TokeoAiFundiAgent
from tokeo.core.ai.guard import TokeoAiGuard
from tokeo.core.ai.sandbox import TokeoAiSandbox


class TokeoAiError(TokeoError):
    """Raised when an ai profile or provider cannot be resolved."""


__all__ = [
    'TokeoAiError',
    'Usage',
    'ToolCall',
    'ToolResult',
    'ChatResult',
    'Invocation',
    'TokeoAiProvider',
    'TokeoAiTool',
    'TokeoAiAgent',
    'TokeoAiFundiAgent',
    'TokeoAiGuard',
    'TokeoAiSandbox',
]
