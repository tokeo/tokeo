# Tokeo SMTPD

The SMTP server library of [Tokeo](https://github.com/tokeo/tokeo) on ```asyncio```: the SMTP dialog with your own event handlers, STARTTLS and immediate TLS, AUTH, PROXY, limits of connections and processings, and message spooling.

```bash
pip install tokeo-smtpd
```

```python
from tokeo.core.smtpd import SmtpdServer, SmtpdEvents
```

It depends only on tokeo-base. The cryptography package is needed only to generate a self-signed test certificate. The guide is [SMTPD.md](https://github.com/tokeo/tokeo/blob/master-2.x/tokeo-smtpd/tokeo/core/smtpd/SMTPD.md); within a Tokeo app use the smtpd extension ```tokeo.ext.smtpd```.
