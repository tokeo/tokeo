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

#### Why not the original diskcache only

Tokeo DiskCache exists because of issue [#357](https://github.com/grantjenks/python-diskcache/issues/357) of DiskCache, which reports CVE-2025-69872: pickled data in the cache directory can run code in the application that reads it. The maintainer considers the risk documented and manageable with the cache directory as trusted storage, sees no reason to rush a breaking change of the serialization and commits to no fix or timeline. We cannot see this position change, and we do not want to leave such a possibility in our projects at all. So Tokeo DiskCache takes only the parts Tokeo needs and offers the same function completely without pickle for security reasons.

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

## Comparison with DiskCache

### Features

| Feature | DiskCache 5.6.3 | Tokeo DiskCache |
|---|---|---|
| Atomic, persistent, thread-safe, process-safe | Yes | Yes |
| Backend | SQLite | SQLite, same database format |
| Eviction, expiry, tags, transactions | Yes | Yes |
| Serialization | Customizable, pickle by default | Raw types as they are, else JSON |
| Loads pickled data | Yes (CVE-2025-69872) | Never, an old pickled value raises a ```ValueError``` |
| Values | Anything pickle can handle | JSON compatible, a tuple comes back as a list |
| Data types | Cache, FanoutCache, Deque, Index | Cache and the Lock recipe |
| New directories | ```0o755``` | ```0o700```, owner only |
| Python | 3 and PyPy | 3.11 and later |
| Integrations | Django | Tokeo |

<br/>

### Benchmark

Median of three runs with 3000 operations each, Linux, Python 3.11. Both are equally fast, the differences are within the noise.

| Value | Package | get | set | delete |
|---|---|---|---|---|
| str of 100 chars | DiskCache | 6.2 µs | 96.3 µs | 72.6 µs |
| str of 100 chars | Tokeo DiskCache | 6.6 µs | 90.3 µs | 75.9 µs |
| dict | DiskCache | 8.5 µs | 95.4 µs | 83.7 µs |
| dict | Tokeo DiskCache | 8.5 µs | 100.8 µs | 79.8 µs |

<br/>
<br/>

It depends on no other package. Within a Tokeo app use the diskcache extension ```tokeo.ext.diskcache```.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - Empowering Python Applications
