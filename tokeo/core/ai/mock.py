"""
Mock ai provider for Tokeo applications.

A deterministic, dependency-free provider for tests and demos. It needs no
model, server, or network, so an agentic flow can be exercised immediately. It
echoes the last user message back with a clear ``[mock]`` marker and reports
fake token usage, which keeps it obvious that no real model was involved.
"""

from tokeo.core.ai import Provider, ChatResult, Usage


class MockProvider(Provider):
    """
    Deterministic provider that fakes a chat completion.

    ### Notes

    : The reply is derived only from the input, so the same messages always
        produce the same result, and no model, server, or network is used.

    """

    def chat(self, profile, messages, tools=None):
        """
        Return a deterministic echo of the last user message.

        ### Args

        - **profile** (dict): The resolved profile (ignored beyond its type)
        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **tools** (list | None): Accepted but not acted upon

        ### Returns

        - **ChatResult**: The faked, deterministic response

        """
        # echo the most recent user turn, so the reply is a pure function of
        # the input and obvious as a mock
        prompt = ''
        for message in messages or []:
            if isinstance(message, dict) and message.get('role') == 'user':
                prompt = message.get('content') or ''
        text = f'[mock] {prompt}' if prompt else '[mock] (no prompt)'
        usage = Usage(
            prompt_tokens=len(prompt.split()),
            completion_tokens=len(text.split()),
            total_tokens=len(prompt.split()) + len(text.split()),
        )
        return ChatResult(
            text=text,
            tool_calls=[],
            usage=usage,
            finish_reason='stop',
            raw={'provider': 'mock', 'echo': prompt},
        )
