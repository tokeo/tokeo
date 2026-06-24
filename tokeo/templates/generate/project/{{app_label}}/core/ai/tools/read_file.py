"""
Read-file tool for the {{ app_name }} ai agent.

Reads a text file strictly below a configured base directory, so an agent can
look things up without roaming the file system: the ```base_dir``` is a tool
setting from the configuration, and any path that escapes it (an absolute
path or a ```..``` traversal) is rejected before anything is opened. Reading is
the harmless half of the file pair; the writing ```append_file``` tool is the
one a policy guard typically denies.

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


class TokeoAiReadFileTool(TokeoAiTool):
    """
    Tool that reads a text file below the configured base directory.

    The ```Meta``` description and parameters are what the model sees; the
    ```base_dir``` is the tool's own setting, overridden per item by its config
    ```options``` and read from ```_meta```.

    """

    class Meta:
        """Tool meta-data sent to the model, plus the tool's own settings."""

        # Short description the model sees
        description = 'read a text file below the configured base directory'

        # JSON-schema object describing the arguments
        parameters = dict(
            type='object',
            properties=dict(path=dict(type='string', description='the file path relative to the base directory')),
            required=['path'],
        )

        # the directory the tool may read from; a tool setting, not
        # model-facing
        base_dir = 'tmp'

    def exec(self, path):
        """
        Read the file and return its text content.

        ### Args

        - **path** (str): The file path relative to the base directory

        ### Returns

        - **str**: The file content

        ### Raises

        - **TokeoAiError**: If the path escapes the base directory or the
            file does not exist

        """
        target = _resolve_below(self._meta.base_dir, path)
        if not target.is_file():
            raise TokeoAiError(f'no such file: {str(path)!r}')
        # the value is the file content; as_str defaults to the same text
        return target.read_text()
