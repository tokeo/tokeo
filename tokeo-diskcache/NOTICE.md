# Tokeo-DiskCache

Copyright 2012-2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>

This product includes software developed by
Tom (Thomas) Freudenberg (https://github.com/tokeo/tokeo).

Licensed under the Apache License, Version 2.0.
A copy of the License is provided in the accompanying LICENSE file
or at http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

## Origin

Tokeo DiskCache is taken from DiskCache 5.6.3
(https://github.com/grantjenks/python-diskcache), licensed under the Apache
License, Version 2.0, Copyright 2016-2023 Grant Jenks.

It takes ```core.py``` and the ```Lock``` recipe of ```recipes.py``` and
changes them: keys and values that are no raw types are stored as JSON
instead of pickle, pickled data is never loaded, and new directories are only
accessible by their owner. The modified files say so in their docstring.
