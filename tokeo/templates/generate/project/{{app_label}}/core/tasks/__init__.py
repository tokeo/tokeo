"""
The {{ app_name }} task collection -- actors, automations, agents,
operators, performers, and steps; lazily loaded so importing the
package stays cheap until a task module is touched.
"""

import lazy_loader as lazy

__getattr__, __dir__, __all__ = lazy.attach(
    __name__,
    submodules=[
        'actors',
        'automate',
        'agents',
        'operators',
        'performers',
        'steps',
    ],
)
