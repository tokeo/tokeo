"""
The guard base class: a check around one tool call. A before guard
may deny the call, an after guard observes or reshapes the outcome.
"""

from cement.core.meta import MetaMixin


class TokeoAiGuard(MetaMixin):
    """
    Base class for guards in the tool-call pipeline.

    A guard inspects, and may shape, a tool call as an ``Invocation`` travels
    through the pipeline. ``Meta.phase`` decides when it runs: a ``before``
    guard runs pre-exec and may deny the call (set ``decision``/``reason``); an
    ``after`` guard runs post-exec and sees the ``result`` or ``error`` (and
    always runs, so it records a denial too). Guards are selected per agent
    (``agent.guards``); with none selected the loop calls the tool directly.

    Its class is resolved from the ``ai.guards`` item ``type`` (a built-in
    short name or a dotted path) and instantiated with the application and the
    item's ``options`` as Meta overrides by the ``app.ai`` handler. Like a
    provider, it holds no mutable per-call state.

    """

    class Meta:
        """Guard meta-data."""

        # 'before' runs pre-exec and may deny; 'after' runs post-exec
        phase = 'after'

    def __init__(self, app, *args, **kw):
        """
        Initialize the guard.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: Keyword arguments for the parent initializer

        """
        super(TokeoAiGuard, self).__init__(*args, **kw)
        self.app = app

    def _setup(self, app):
        """
        Set up the guard after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass

    def check(self, invocation):
        """
        Inspect and possibly shape an invocation.

        ### Args

        - **invocation** (Invocation): The tool call passing through the
            pipeline; a before guard may set ``decision``/``reason`` to deny
            it, an after guard may read or adjust ``result``/``error``

        """
        raise NotImplementedError
