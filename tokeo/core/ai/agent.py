"""
The agent base class and the standard fundi agent. An agent is the
composition root (tools, guards, sandboxes, deny, budgets); the loop
itself lives in the app.ai handler.
"""

from cement.core.meta import MetaMixin


class TokeoAiAgent(MetaMixin):
    """
    Declarative base class for agents, the composition root of an ai call.

    An agent binds the building blocks of a task together: which tools are
    active, which guards wrap each call, which sandboxes contain execution,
    and how many model calls the loop may take. The model itself is not part
    of the agent; it is bound late through the selected profile, so the same
    agent can run against the mock, a local model, or a hosted one. The class
    is resolved from the ```ai.agents``` item ```type``` (a built-in short name or
    a dotted path) by the ```app.ai``` handler, which passes the agent's
    configuration entry as keyword arguments.

    This base is declarative only: it carries the composition (```Meta```) and
    the lifecycle, and is not used directly. tokeo ships exactly one concrete
    agent, ```TokeoAiFundiAgent``` (the ```fundi``` type); a project may add its
    own by subclassing this. The agent loop itself lives in the ```app.ai```
    handler, not on the agent, so an agent only varies the composition, not
    the orchestration.

    ### Notes

    : ```Meta``` declares the configurable keys (```tools```, ```guards```,
        ```sandboxes```, ```deny```, ```max_steps```, ```max_loops```) with neutral
        defaults; the ```options``` of the ```ai.agents``` entry override them at
        build time (the cement Meta keyword override), and they are read from
        ```_meta```.

    """

    class Meta:
        """Agent composition, overridden per agent by its entry's options."""

        # the tool selection (item or group names); merged with the profile's
        tools = []

        # the guard selection (guard names) for the tool-call pipeline
        guards = []

        # the ordered sandbox chain (sandbox names): a tool runs in the first
        # sandbox whose tools contain it; when none does the call is denied,
        # so an ```in_process``` sandbox with ```tools: _all``` placed last is the
        # opt-in catch-all that lets the remaining tools run in process
        sandboxes = []

        # tools (item or group names) this agent forbids outright, before any
        # sandbox lookup; a hard exclusion, unlike a sandbox ```except```
        deny = []

        # per-agent cap on tool rounds (0 = unlimited); None means use the
        # handler's base default
        max_steps = None

        # per-agent cap on consecutive rounds without one successful call
        # (0 = unlimited); None means use the handler's base default
        max_loops = None

    def __init__(self, app, *args, **kw):
        """
        Initialize the agent.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: The agent's config entry; keys matching ```Meta``` override
            its defaults

        """
        super(TokeoAiAgent, self).__init__(*args, **kw)
        self.app = app

    def _setup(self, app):
        """
        Set up the agent after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass


class TokeoAiFundiAgent(TokeoAiAgent):
    """
    The standard agent, registered as the ```fundi``` type.

    fundi (Swahili for master/craftsman) is the composition root that wields
    the tools: it inherits the declarative composition of ```TokeoAiAgent``` and
    is the one concrete agent tokeo ships. It adds no behaviour of its own --
    the loop lives in the ```app.ai``` handler, the agent only composes which
    tools, guards, and sandboxes that loop uses. A project that needs a
    different composition configures another ```ai.agents``` entry of this type;
    a project that needs a different orchestration subclasses ```TokeoAiAgent```.

    """

    pass
