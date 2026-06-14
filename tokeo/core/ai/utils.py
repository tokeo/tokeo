"""
Small, dependency-light helpers shared across the ai layer.

Kept here (not in the ext) so any consumer -- the cli controller, a provider,
a project's own code -- can use them without importing the cement extension.
"""

import yaml

from tokeo.core.ai import TokeoAiError


def coerce_model_param_value(raw):
    """
    Coerce a raw ```key=value``` value the way the yaml config handler coerces
    an environment override.

    Runs the string through ```yaml.safe_load```, so ```0.2```/```42```/
    ```true```/```null``` get their proper types and anything else stays a
    string. This mirrors the env-override coercion on purpose -- same rule for
    a value typed on the command line as for one injected via the environment.
    It is a deliberate four-line clone rather than a cross-module import, and
    the env-only ```!```-tag rejection is not wanted here.

    ### Args

    - **raw** (str): The raw value text (already stripped)

    ### Returns

    - The coerced scalar, or the original string when it is not a yaml scalar

    """
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def parse_model_params(pairs):
    """
    Turn a list of ```key=value``` strings into a model_params dict.

    The value is coerced like a yaml scalar (see ```coerce_model_param_value```);
    a null or empty value removes the key, so a call can drop a parameter and
    fall back to the profile's value. Shared by ```ai ask```, the ```ai chat```
    start flags and the interactive chat switches, so one rule holds everywhere.

    ### Args

    - **pairs** (list|None): The raw ```key=value``` strings to parse

    ### Returns

    - **dict**: The parsed and coerced model parameters

    ### Raises

    - **TokeoAiError**: On a token without ```=``` or with an empty key

    """
    params = {}
    for pair in pairs or []:
        key, sep, raw = pair.partition('=')
        key = key.strip()
        if not sep or not key:
            raise TokeoAiError(f'model_param expects key=value, got {pair!r}')
        value = coerce_model_param_value(raw.strip())
        if value is None:
            # null or empty removes the key, shell-independent (no quoting trap)
            params.pop(key, None)
        else:
            params[key] = value
    return params
