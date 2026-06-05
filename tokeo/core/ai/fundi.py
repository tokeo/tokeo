"""
Fundi ai provider for Tokeo applications.

The ``fundi`` provider stands for the application's own local model. Version
``fundi0.0`` is a deterministic stand-in that needs no server and no network,
so an agentic flow can be exercised out of the box and the workings of a model
and an agent can be explained clearly. Later a small trained model
(``fundi1.0``) can take its place behind the same provider, selected through
the profile's ``options.model``.
"""

from tokeo.core.ai import TokeoAiProvider, ChatResult, Usage


class TokeoAiFundiProvider(TokeoAiProvider):
    """
    Built-in local model provider, deterministic at version ``fundi0.0``.

    ### Notes

    : The reply is derived only from the input and is labelled with the
        profile's ``options.model``, so the same messages always produce the
        same result and it stays obvious which fundi version answered.

    """

    def chat(self, profile, messages, tools=None):
        """
        Return a deterministic, model-labelled echo of the last user message.

        ### Args

        - **profile** (dict): The resolved profile; ``options.model`` labels
            the reply
        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **tools** (list | None): Accepted but not acted upon

        ### Returns

        - **ChatResult**: The faked, deterministic response

        """
        # the model from the profile options labels the reply, so the fundi
        # version is always visible in the output and in the raw payload
        model = ((profile or {}).get('options') or {}).get('model') or 'fundi0.0'
        prompt = ''
        for message in messages or []:
            if isinstance(message, dict) and message.get('role') == 'user':
                prompt = message.get('content') or ''
        text = f'[{model}] {prompt}' if prompt else f'[{model}] (no prompt)'
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
            raw={'provider': 'fundi', 'model': model, 'echo': prompt},
        )
