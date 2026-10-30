# Tokeo SMTPD

The SMTP server library of [Tokeo](https://github.com/tokeo/tokeo), an asyncio port of the battle-tested Ruby [midi-smtp-server](https://github.com/4commerce-technologies-AG/midi-smtp-server), like the SMTP dialog with your own event handlers, STARTTLS and immediate TLS, AUTH, PROXY, limits of connections and processings, message spooling and others.


<br/>

## Getting Started

### Installing Tokeo SMTPD

```bash
# Install directly from Pypi.org
pip install tokeo-smtpd
```

### Usage

```python
from tokeo.core.smtpd import SmtpdServer, SmtpdEvents
```

<br/>
<br/>

It depends only on tokeo-base, the cryptography package is needed only to generate a self-signed test certificate. The guide [SMTPD.md](https://github.com/tokeo/tokeo/blob/master-2.x/tokeo-smtpd/tokeo/core/smtpd/SMTPD.md) explains the events, modes and settings. Within a Tokeo app use the smtpd extension ```tokeo.ext.smtpd```.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
