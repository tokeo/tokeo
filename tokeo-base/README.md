# Tokeo Base

The exceptions and the small dependency-free helpers of [Tokeo](https://github.com/tokeo/tokeo): checks and defaults, dates, dicts, json, sorting, sanitizing, urls, tls, module loading and time limits.

```bash
pip install tokeo-base
```

```python
from tokeo.core.exc import TokeoError
from tokeo.core.utils.validate import is_number_gt
```

Tokeo itself and tokeo-smtpd depend on it, it needs no other package.
