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
  Full text: `assets/fonts/Inter-OFL.txt`.
- Source Code Pro — Copyright Adobe (Adobe Systems Incorporated);
  SIL Open Font License 1.1.
  Full text: `assets/fonts/SourceCodePro-OFL.txt`.

Styles and scripts:

- The bundled `tailwind.css` is generated with Tailwind CSS
  (Copyright Tailwind Labs, Inc.; MIT License); the MIT banner is retained
  in the file.
- highlight.js (BSD-3-Clause) and Mermaid (MIT) are bundled under
  `assets/` for client-side syntax highlighting and diagrams.

The documentation renderer itself uses pdoc (https://pdoc.dev, MIT) and
Python-Markdown (BSD-3-Clause) at runtime, declared as dependencies.
