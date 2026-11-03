# Tokeo Base

The exceptions and the small dependency-free helpers of [Tokeo](https://github.com/tokeo/tokeo) like validations, dates, sorting, sanitizing, urls, tls, module loading, time limits and others.

<br/>

## Features

- Exceptions with ```TokeoError``` and ```TokeoBaseException``` as the bases of all errors of Tokeo
- Checks of values like ```is_int_gt```, ```is_number_between``` or ```is_filled_str```, and defaults for missing values
- Dates in UTC, read from and written to ISO strings the same way on each Python version
- Deep merge of settings, and redaction of values before they are logged
- Keywords stripped to safe characters and strings quoted for the shell
- Time limits of functions by the decorator ```@timelimit```
- TLS contexts, urls with credentials, JSON encoders, random ids and the expiry of a JWT
- Modules and objects loaded by their dotted name, and a local file loaded in place of an installed module
- Paths in memory for APIs that only accept a file, e.g. a certificate that never touches disk, on Linux, macOS and BSD

<br/>

## Getting Started

### Installing Tokeo Base

```bash
# Install directly from Pypi.org
pip install tokeo-base
```

### Usage

```python
from tokeo.core.exc import TokeoError
from tokeo.core.utils.dict import deep_merge
from tokeo.core.utils.sort import sort_key_by_lex_and_num_ordered
from tokeo.core.utils.timelimit import timelimit
from tokeo.core.utils.validate import is_number_gt

# merge the settings of a file into the defaults
settings = deep_merge({'retries': 3, 'smtp': {'host': 'localhost', 'port': 25}}, {'smtp': {'port': 2525}})
print(settings)

if not is_number_gt(settings['retries'], 0):
    raise TokeoError('retries must be above 0')

# sort names with numbers as people read them
print(sorted(['host10', 'host2', 'host1'], key=sort_key_by_lex_and_num_ordered))


@timelimit(5)
def job():
    return 'done within 5 seconds'


print(job())
```

It prints:

```
{'retries': 3, 'smtp': {'host': 'localhost', 'port': 2525}}
['host1', 'host2', 'host10']
done within 5 seconds
```

<br/>
<br/>

It depends on no other package. Tokeo, tokeo-smtpd and tokeo-pocketbase build on it.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
