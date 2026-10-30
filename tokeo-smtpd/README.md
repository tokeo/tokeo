# Tokeo SMTPD

The SMTP server library of [Tokeo](https://github.com/tokeo/tokeo), an asyncio port of the battle-tested Ruby [midi-smtp-server](https://github.com/4commerce-technologies-AG/midi-smtp-server), like the SMTP dialog with your own event handlers, STARTTLS and immediate TLS, AUTH, PROXY, limits of connections and processings, message spooling and others.

<br/>

## Features

- Events for each step of the dialog, as ```def```, ```async def``` or ```@threaded``` for slow work
- STARTTLS or immediate TLS, with a self-signed test certificate when none is configured
- AUTH PLAIN and LOGIN, optional or required
- 8BITMIME, SMTPUTF8 and PIPELINING, each to switch on
- PROXY protocol for servers behind a load balancer
- Limits of connections and processings, answered by ```421``` when full
- Spooling of large messages to disk instead of memory
- Graceful shutdown that lets messages in transfer finish

<br/>

## Getting Started

### Installing Tokeo SMTPD

```bash
# Install directly from Pypi.org
pip install tokeo-smtpd
```

### Usage

You write a handler with the events you care about, the server runs the SMTP dialog. The library does nothing with your mail by itself, what happens to it is up to your handler.

```python
import asyncio
from tokeo.core.smtpd import SmtpdServer, SmtpdEvents, Smtpd550Exception


class MailHandler(SmtpdEvents):

    def on_rcpt_to_event(self, ctx, rcpt_to):
        # only accept mail addressed to our domain, reject the rest
        if not rcpt_to.strip('<>').endswith('@example.com'):
            raise Smtpd550Exception             # client sees "550 ..."
        return True                             # accept the address as sent

    def on_message_data_event(self, ctx):
        # the complete message has arrived; do something with it
        print('from:', ctx.envelope.mail_from)
        print('to:  ', ctx.envelope.rcpt_tos)
        print(bytes(ctx.message.data).decode('utf-8', 'replace'))


async def main():
    server = SmtpdServer(MailHandler(), ports=2525, hosts='127.0.0.1')
    await server.start()          # bind and serve in the background
    try:
        await server.join()       # run until stopped
    finally:
        await server.stop()


asyncio.run(main())
```

Send a test mail, e.g. with ```smtplib``` of Python. The handler prints it, a recipient outside of ```example.com``` gets a ```550```.

```python
import smtplib

with smtplib.SMTP('127.0.0.1', 2525) as client:
    client.sendmail('alice@test.org', ['bob@example.com'], 'Subject: Hello\r\n\r\nHi Bob!\r\n')
```

<br/>
<br/>

It depends only on tokeo-base, the cryptography package is needed only to generate a self-signed test certificate. The guide [SMTPD.md](https://github.com/tokeo/tokeo/blob/master-2.x/tokeo-smtpd/tokeo/core/smtpd/SMTPD.md) explains the events, modes and settings. Within a Tokeo app use the smtpd extension ```tokeo.ext.smtpd```.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
