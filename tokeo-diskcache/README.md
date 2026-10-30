# Tokeo DiskCache

The disk and file backed cache of [DiskCache](https://github.com/grantjenks/python-diskcache) 5.6.3 for [Tokeo](https://github.com/tokeo/tokeo), without pickle. Values are stored as JSON, pickled data is never loaded, so a cache directory written by someone else cannot run code in your application (CVE-2025-69872).

<br/>

## Features

- The ```Cache``` of DiskCache with its SQLite database, expiry, tags, eviction and transactions
- The ```Lock``` recipe for locks across threads and processes
- Keys and values as ```str```, ```bytes```, ```int``` and ```float``` like DiskCache, everything else as JSON
- No pickle, a pickled value of an older cache is refused by a ```ValueError```
- New cache directories are only accessible by their owner
- The same database format as DiskCache 5.6.3

<br/>

## Getting Started

### Installing Tokeo DiskCache

```bash
# Install directly from Pypi.org
pip install tokeo-diskcache
```

### Usage

The cache works like the one of DiskCache, only the import differs.

```python
from tokeo.core.diskcache import Cache, Lock

cache = Cache('./my_diskcache')
cache.set('counter', 1)
cache.set('config', {'retries': 3, 'hosts': ['a', 'b']}, expire=3600)
print(cache.get('counter'), cache.get('config'))

with Lock(cache, 'report'):
    # only one process at a time runs this block
    cache.incr('counter')
```

Values must be JSON compatible. A tuple comes back as a list, a ```datetime``` or an own object raises a ```TypeError``` on ```set```.

<br/>
<br/>

It depends on no other package. Within a Tokeo app use the diskcache extension ```tokeo.ext.diskcache```.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
