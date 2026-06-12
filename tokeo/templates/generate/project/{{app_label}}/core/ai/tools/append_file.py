"""
Append-file tool for the {{ app_name }} ai agent.

Appends a line of text to one configured file below a configured base
directory -- the writing half of the file pair, and so the natural target for
a policy guard: an agent that may read should not silently write, and denying
```append_file``` by name shows action-level governance on a tool that really
exists. The target ```file``` and the ```base_dir``` are tool settings from the
configuration; the model only supplies the text.

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ```ai.tools``` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

from pathlib import Path

from tokeo.core.ai import TokeoAiError, TokeoAiTool


def _resolve_below(base_dir, path):
    # resolve the requested path strictly below the base directory; an
    # absolute path or a ```..``` traversal must never escape the sandbox the
    # configuration defines
    base = Path(base_dir).resolve()
    target = (base / str(path)).resolve()
    if target == base or not target.is_relative_to(base):
        raise TokeoAiError(f'path {str(path)!r} escapes the tool base directory')
    return target


class TokeoAiAppendFileTool(TokeoAiTool):
    """
    Tool that appends a line of text to the configured file.

    The ```Meta``` description and parameters are what the model sees; the
    ```base_dir``` and the target ```file``` are the tool's own settings,
    overridden per item by its config ```options``` and read from ```_meta```.

    """

    class Meta:
        """Tool meta-data sent to the model, plus the tool's own settings."""

        # Short description the model sees
        description = 'append a line of text to the configured notes file'

        # JSON-schema object describing the arguments
        parameters = dict(
            type='object',
            properties=dict(text=dict(type='string', description='the text line to append')),
            required=['text'],
        )

        # the directory and file the tool may write to; tool settings, not
        # model-facing
        base_dir = 'tmp'
        file = 'notes.txt'

    def exec(self, text):
        """
        Append the text as one line to the configured file.

        ### Args

        - **text** (str): The text line to append

        ### Returns

        - **str**: A short confirmation naming the file

        ### Raises

        - **TokeoAiError**: If the configured file escapes the base directory

        """
        target = _resolve_below(self._meta.base_dir, self._meta.file)
        with open(target, 'a') as handle:
            handle.write(str(text).rstrip('\n') + '\n')
        return f'appended to {self._meta.file!r}'
