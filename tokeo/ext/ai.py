"""
Tokeo ai extension.

Wires the ai core into a Cement application: registers the built-in providers,
opens a hook so third parties can register their own provider, exposes the
``app.ai`` handler, and adds the ``fundi`` command group for the agentic and
ai-facing side.

The technical namespace stays ``ai`` (this module, the ``tokeo.core.ai``
package, and the ``ai`` config section). ``fundi`` is the user-facing brand
for the agentic part and is the name of the command group.

```yaml
ai:
  default: fundi0
  profiles:
    fundi0:
      type: fundi
      model: fundi0.0
```

### Notes

    : With no profile selected and no ``ai.default`` configured, ``app.ai``
        falls back to the built-in ``fundi`` model, so ``fundi ask`` answers
        out of the box.

"""

from cement import ex
from cement.core.meta import MetaMixin

from tokeo.ext.argparse import Controller
from tokeo.core.ai import (
    TokeoAiError,
    register_provider,
    get_provider,
    find_profile,
)
from tokeo.core.ai.mock import MockProvider
from tokeo.core.ai.fundi import FundiProvider


class TokeoAi(MetaMixin):
    """
    AI handler for Tokeo applications, reached through ``app.ai``.

    Resolves a profile from the ``ai`` config section (by name, or by a field
    such as ``model`` or ``purpose``) and hands the resolved profile to the
    selected provider. Holds no mutable per-call state, so it is safe to use
    from several threads at once (for example dramatiq workers or scheduler
    jobs).

    ### Notes

    : The handler is registered as ``ai`` and is reached through ``app.ai``.
        It is a thin dispatcher over the registered providers, not a wrapper
        around any provider's full surface.

    """

    class Meta:
        """Handler meta-data and configuration defaults."""

        # Unique identifier for this handler
        label = 'tokeo.ai'

        # Configuration section name in the application config
        config_section = 'ai'

        # Default configuration settings
        config_defaults = dict(
            # profile used when a call names none
            default='mock',
            # named profiles; each binds a provider type to its details. the
            # built-in mock profile lets a fresh app answer without any setup
            profiles=dict(
                mock=dict(
                    type='mock',
                    model='mock',
                    purpose='mocking',
                ),
            ),
        )

    def __init__(self, app, *args, **kw):
        """
        Initialize the ai handler.

        Stores the application reference only; the configuration is merged in
        the ``_setup`` method once the framework has loaded it.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments passed to the parent initializer
        - ****kw**: Keyword arguments passed to the parent initializer

        """
        super(TokeoAi, self).__init__(*args, **kw)
        self.app = app

    def _setup(self, app):
        """
        Set up the ai handler.

        Called by the framework after the configuration has been loaded.
        Merges the default configuration so the ``ai`` section always exists,
        without overriding values the application provides.

        ### Args

        - **app**: The Tokeo application instance

        """
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)

    def _config(self, key, **kwargs):
        """
        Get a configuration value from the extension's config section.

        A simple wrapper around the application's ``config.get`` that uses the
        correct configuration section.

        ### Args

        - **key** (str): Configuration key to retrieve
        - ****kwargs**: Additional arguments passed to ``config.get``

        ### Returns

        - **Any**: Configuration value for the specified key

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def _resolve(self, profile=None, model=None, purpose=None):
        # at most one selector may be given; with none, use the configured
        # default, and with no default at all fall back to the built-in fundi
        # model so a fresh app can answer without any setup
        keys = {'profile': profile, 'model': model, 'purpose': purpose}
        active = {k: v for k, v in keys.items() if v is not None}
        if len(active) > 1:
            raise TokeoAiError('select a profile by only one of profile, model or purpose')
        if active:
            key, value = next(iter(active.items()))
            return find_profile(self.app, key, value)
        # no selector: use the configured default (which defaults to the mock
        # profile via the Meta config_defaults)
        default = self._config('default')
        if not default:
            raise TokeoAiError('no ai profile selected and no ai.default configured')
        return find_profile(self.app, 'profile', default)

    def chat(self, messages, tools=None, profile=None, model=None, purpose=None):
        """
        Send messages to the resolved model and return a ``ChatResult``.

        ### Args

        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **tools** (list | None): Optional tool definitions for the call
        - **profile** (str | None): Select a profile by name
        - **model** (str | None): Select the first enabled profile by model
        - **purpose** (str | None): Select the first enabled profile by purpose

        ### Returns

        - **ChatResult**: The normalized response

        ### Raises

        - **TokeoAiError**: If no profile resolves or it carries no ``type``

        """
        name, profile = self._resolve(profile=profile, model=model, purpose=purpose)
        provider_type = profile.get('type')
        if not provider_type:
            raise TokeoAiError(f'ai profile {name!r} is missing a type')
        return get_provider(provider_type).chat(profile, messages, tools=tools)

    def ask(self, prompt, tools=None, profile=None, model=None, purpose=None):
        """
        Send a single user prompt and return the reply text.

        ### Args

        - **prompt** (str): The user prompt
        - **tools** (list | None): Optional tool definitions for the call
        - **profile** (str | None): Select a profile by name
        - **model** (str | None): Select the first enabled profile by model
        - **purpose** (str | None): Select the first enabled profile by purpose

        ### Returns

        - **str**: The assistant text

        """
        messages = [{'role': 'user', 'content': prompt}]
        result = self.chat(messages, tools=tools, profile=profile, model=model, purpose=purpose)
        return result.text


class FundiController(Controller):
    """
    Fundi command group for the agentic and ai-facing commands.

    """

    class Meta:
        label = 'fundi'
        stacked_type = 'nested'
        stacked_on = 'base'
        description = 'talk to the configured model and run agentic tasks'
        help = 'ai and agentic commands'

    @ex(
        help='ask the configured model a single prompt',
        arguments=[
            (['prompt'], dict(help='the prompt text', nargs='?')),
            (['--profile'], dict(help='select an ai profile by name', dest='profile')),
            (['--model'], dict(help='select an ai profile by model', dest='model')),
            (['--purpose'], dict(help='select an ai profile by purpose', dest='purpose')),
        ],
    )
    def ask(self):
        prompt = self.app.pargs.prompt
        if not prompt:
            raise TokeoAiError('no prompt given; usage: fundi ask "your question"')
        text = self.app.ai.ask(
            prompt,
            profile=self.app.pargs.profile,
            model=self.app.pargs.model,
            purpose=self.app.pargs.purpose,
        )
        self.app.print(text)


def ai_extend_app(app):
    """
    Cement post-setup hook: create ``app.ai`` and register providers.

    Extends the application with the ai handler, sets it up, and then runs the
    ``tokeo_ai_register_providers`` hook so extensions and plugins can register
    their own provider from the outside, once every extension has been loaded.

    ### Args

    - **app**: The application instance

    """
    app.extend('ai', TokeoAi(app))
    app.ai._setup(app)
    for res in app.hook.run('tokeo_ai_register_providers', app):
        pass


def load(app):
    """
    Load the ai extension.

    ### Args

    - **app**: The application instance

    ### Notes

    - Built-in providers are registered directly, so they are available
        without any configuration
    - Defines the ``tokeo_ai_register_providers`` hook, so other extensions or
        plugins can register their own ai providers from the outside
    - Registers a post_setup hook that creates ``app.ai`` and runs the
        provider registration hook

    """
    # built-in providers are always available without any configuration; mock
    # is the neutral test double, fundi is the application's own local model
    register_provider('mock', MockProvider())
    register_provider('fundi', FundiProvider())
    # open extension point: an extension or plugin can register its own ai
    # provider from the outside by hooking 'tokeo_ai_register_providers'; it
    # runs at post_setup, once every extension has been loaded
    app.hook.define('tokeo_ai_register_providers')
    app.hook.register('post_setup', ai_extend_app)
    app.handler.register(FundiController)
