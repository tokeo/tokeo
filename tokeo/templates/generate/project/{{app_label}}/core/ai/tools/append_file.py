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
from tokeo.core.ai.tool import create_tool_result
from tokeo.core.utils.date import to_utc, to_utc_timestring


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

        The value carries the write outcome -- the file name, its byte size
        before and after, whether it was newly created, and its filesystem
        timestamps; the model sees only true/false (success) as the as_str.

        ### Args

        - **text** (str): The text line to append

        ### Returns

        - **ToolResult**: A dict value with the file name, size_before and
            size_after in bytes, created (true if the file was new),
            created_at and updated_at as UTC timestrings; as_str is true/false

        ### Raises

        - **TokeoAiError**: If the configured file escapes the base directory

        """
        target = _resolve_below(self._meta.base_dir, self._meta.file)
        # check existence and size BEFORE the append: a missing file is created
        # by append mode, so this is the only point that can tell new from empty
        created = not target.is_file()
        size_before = 0 if created else target.stat().st_size
        with open(target, 'a') as handle:
            handle.write(str(text).rstrip('\n') + '\n')
        # read the filesystem timestamps after the write; st_birthtime is the
        # real creation time where the platform has it (mac/bsd), st_ctime the
        # best fallback elsewhere (on linux it is the inode change time)
        stat = target.stat()
        created_at = getattr(stat, 'st_birthtime', stat.st_ctime)
        result = dict(
            file=self._meta.file,
            size_before=size_before,
            size_after=stat.st_size,
            created=created,
            created_at=to_utc_timestring(to_utc(created_at)),
            updated_at=to_utc_timestring(to_utc(stat.st_mtime)),
        )
        # the model sees the success flag; the dict rides along for a follow-up
        return create_tool_result(result, as_str='true')
