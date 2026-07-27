# Tokeo

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

## Third-party components

The generated API documentation theme ```tokeo/templates/pdoc/html/```
bundles the following third-party assets. Each retains its own license.

Web fonts (self-hosted under ```assets/fonts/```):

- Inter — Copyright The Inter Project Authors; SIL Open Font License 1.1.
  Full text: ```assets/fonts/Inter-OFL.txt```.
- Source Code Pro — Copyright Adobe (Adobe Systems Incorporated);
  SIL Open Font License 1.1.
  Full text: ```assets/fonts/SourceCodePro-OFL.txt```.

Styles and scripts:

- The bundled ```assets/tailwind.min.css``` is generated with Tailwind CSS
  (Copyright Tailwind Labs, Inc.; MIT License); the MIT banner is retained
  in the file. The accompanying ```assets/tokeo.theme.css``` is tokeo's own
  plain-CSS theme (Apache-2.0, part of this product).
- highlight.js — Copyright (c) 2006 Ivan Sagalaev; BSD-3-Clause. Bundled as
  ```assets/highlight.min.js``` together with the themes under
  ```assets/hljs/styles/```, each of which retains its own banner.
  Full text: ```assets/highlightjs-LICENSE.txt```.
- Mermaid — Copyright (c) 2014-2022 Knut Sveidqvist; MIT License. Bundled as
  ```assets/mermaid.min.js```. That build embeds further third-party
  components under their own licenses; see the note in the license file.
  Full text: ```assets/mermaid-LICENSE.txt```.

The documentation renderer itself uses pdoc (https://pdoc.dev, MIT-0) and
Python-Markdown (BSD-3-Clause) at runtime, declared as dependencies.
