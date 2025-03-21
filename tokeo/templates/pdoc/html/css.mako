<%!
    from pdoc.html_helpers import minify_css
%>

<%def name="mobile()" filter="minify_css">
  :root {
    --highlight-color: #fe9;
    --primary-color: #3a7ab9;
    --secondary-color: #2a5885;
    --accent-color: #4568dc;
    --background-gradient: linear-gradient(135deg, rgba(245, 247, 250, 0.1) 0%, rgba(228, 232, 235, 0.1) 100%);
    --header-gradient: linear-gradient(135deg, #ff68dc 0%, #3a6073 100%);
    --card-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
    --text-color: #333;
    --text-light: #666;
  }

  .flex {
    display: flex !important;
  }
  .flex-col {
    flex-direction: column;
    flex-wrap: wrap;
  }

  body {
    line-height: 1.5em;
    background: var(--background-gradient);
    color: var(--text-color);
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    display: flex;
    flex-direction: column;
    min-height: 100vh;
  }

  #content {
    padding: 20px;
  }
  #sidebar {
    padding: 1.5em;
    overflow: hidden;
    background: rgba(255, 255, 255, 0.8);
    backdrop-filter: blur(10px);
    border-right: 1px solid rgba(0, 0, 0, 0.05);
  }
  #sidebar > *:last-child {
    margin-bottom: 2cm;
  }
  #module-list {
    width: 100%;
    padding: 1em;
    margin: 0 auto;
  }

  % if lunr_search is not None:
  #lunr-search {
    width: 100%;
    font-size: 1em;
    padding: 8px 12px;
    border: 1px solid rgba(0, 0, 0, 0.1);
    border-radius: 4px;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05) inset;
  }
  % endif

  /* Header Styles and Animations */
  .page-header {
    background: var(--header-gradient);
    color: white;
    padding: 2em;
    margin-bottom: 2em;
    border-radius: 8px;
    box-shadow: var(--card-shadow);
    text-align: center;
    position: relative;
    overflow: hidden;
    animation: headerLoading 0.4s ease 0.1s forwards;
    opacity: 0;
    transform: translateY(-10px);
  }

  .page-header:before {
    content: "";
    position: absolute;
    top: -50%;
    left: -50%;
    width: 200%;
    height: 200%;
    background: radial-gradient(circle, rgba(255,255,255,0.1) 0%, rgba(255,255,255,0) 70%);
    animation: pulse 15s infinite;
  }

  .page-header:after {
    content: "";
    position: absolute;
    bottom: 0;
    left: 0;
    width: 100%;
    height: 6px;
    background: linear-gradient(90deg,
      rgba(255,255,255,0.3) 0%,
      rgba(255,255,255,0.6) 25%,
      rgba(255,255,255,0.6) 75%,
      rgba(255,255,255,0.3) 100%);
    animation: shimmer 3s infinite;
  }

  @keyframes shimmer {
    0% { background-position: -100% 0; }
    100% { background-position: 100% 0; }
  }

  @keyframes pulse {
    0% { transform: scale(1); opacity: 0.5; }
    50% { transform: scale(1.05); opacity: 0.2; }
    100% { transform: scale(1); opacity: 0.5; }
  }

  @keyframes headerLoading {
    from {
      opacity: 0;
      transform: translateY(-10px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .page-header.loaded {
    transform: translateY(0);
    opacity: 1;
  }

  .page-header h1 {
    margin: 0;
    font-weight: 600;
    position: relative;
    text-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
  }

  .page-header p {
    margin: 0.5em 0 0;
    opacity: 0.9;
    position: relative;
  }

  /* Module Card Styles */
  .module-card {
    transition: all 0.3s ease;
  }

  .module-card:hover {
    transform: translateY(-3px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  }

  .http-server-breadcrumbs {
    font-size: 130%;
    margin: 0 0 15px 0;
    background: rgba(255, 255, 255, 0.7);
    padding: 8px 15px;
    border-radius: 4px;
  }

  /* Footer Styles */
  #footer {
    margin-top: auto;
    width: 100%;
    font-size: 0.75em;
    padding: 15px 30px;
    border-top: 1px solid rgba(0, 0, 0, 0.1);
    text-align: center;
    background: rgba(255, 255, 255, 0.7);
    backdrop-filter: blur(5px);
  }
  #footer p {
    margin: 0 0 0 1em;
    display: inline-block;
  }
  #footer p:last-child {
    margin-right: 30px;
  }

  /* Typography */
  h1, h2, h3, h4, h5 {
    font-weight: 400;
  }
  h1 {
    font-size: 2.5em;
    line-height: 1.1em;
  }
  h2 {
    font-size: 1.75em;
    margin: 2em 0 0.5em 0;
  }
  h3 {
    font-size: 1.6em;
    margin: 1.6em 0 0.7em 0;
  }
  h4 {
    font-size: 1.4em;
    margin: 0;
  }
  h1:target,
  h2:target,
  h3:target,
  h4:target,
  h5:target,
  h6:target {
    background: var(--highlight-color);
    padding: 0.2em 0;
  }

  /* Links */
  a {
    color: var(--primary-color);
    text-decoration: none;
    transition: color 0.2s ease-in-out;
  }

  a:visited {
    color: var(--secondary-color);
  }

  a:hover {
    color: var(--accent-color);
  }

  .title code {
    font-weight: bold;
  }

  h2[id^="header-"] {
    margin-top: 2em;
  }

  .ident {
    color: #900;
    font-weight: bold;
  }
  .decorator {
    color: #006799;
    font-weight: bold;
  }

  /* Code Formatting */
  pre code {
    font-size: 0.8em;
    line-height: 1.4em;
    padding: 1em;
    display: block;
  }
  code {
    background: #f3f3f3;
    font-family: "DejaVu Sans Mono", monospace;
    padding: 1px 4px;
    overflow-wrap: break-word;
  }
  h1 code { background: transparent; }

  pre {
    border-top: 1px solid #ccc;
    border-bottom: 1px solid #ccc;
    margin: 1em 0;
  }

  /* Module List Grid */
  #http-server-module-list {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 1.5em;
    margin-top: 2em;
  }
  #http-server-module-list div.module-card {
    background: white;
    border-radius: 8px;
    padding: 1.5em;
    box-shadow: var(--card-shadow);
    transition: transform 0.3s ease, box-shadow 0.3s ease;
    display: flex;
    flex-direction: column;
    position: relative;
    overflow: hidden;
    border: 1px solid rgba(0, 0, 0, 0.05);
  }
  #http-server-module-list div.module-card:hover {
    transform: translateY(-5px);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  }
  #http-server-module-list div.module-card:after {
    content: "";
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 4px;
    background: var(--header-gradient);
  }
  #http-server-module-list dt {
    min-width: 10%;
    margin-bottom: 0.5em;
  }
  #http-server-module-list dt a {
    color: var(--secondary-color);
    font-weight: 600;
    font-size: 1.2em;
    text-decoration: none;
    transition: color 0.2s;
  }
  #http-server-module-list dt a:hover {
    color: var(--accent-color);
  }
  #http-server-module-list dd {
    margin: 0.75em 0 0;
    color: var(--text-light);
    line-height: 1.5;
  }

  /* Index and TOC Styles */
  .toc ul,
  #index {
    list-style-type: none;
    margin: 0;
    padding: 0;
  }
  #index code {
    background: transparent;
  }
  #index h3 {
    border: none;
  }
  #index ul {
    padding: 0;
  }
  #index h4 {
    margin-top: 0.6em;
    font-weight: bold;
  }

  /* Multi-column layouts */
  @media (min-width: 200ex) { #index .two-column { column-count: 2; } }
  @media (min-width: 300ex) { #index .two-column { column-count: 3; } }

  /* Definition Lists */
  dl {
    margin-bottom: 2em;
  }
  dl dl:last-child {
    margin-bottom: 4em;
  }
  dd {
    margin: 0 0 1em 0;
  }
  #header-classes + dl > dd {
    margin-bottom: 3em;
  }
  dd dd {
    margin-left: 2em;
  }
  dd p {
    margin: 10px 0;
  }

  /* Name Styling */
  .name {
    background: linear-gradient(135deg, #f0f6fa 0%, #e0eff4 100%);
    font-size: 0.85em;
    padding: 8px 14px;
    display: inline-block;
    min-width: 40%;
    border-radius: 6px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    border: 1px solid rgba(0, 0, 0, 0.03);
    transition: all 0.2s ease;
  }
  .name:hover {
    background: linear-gradient(135deg, #e5f1f4 0%, #9bbeca 100%);
    box-shadow: 0 2px 5px rgba(0, 0, 0, 0.08);
    transform: translateY(-1px);
  }
  dt:target .name {
    background: var(--highlight-color);
  }
  .name > span:first-child {
    white-space: nowrap;
  }
  .name.class > span:nth-child(2) {
    margin-left: 0.4em;
  }
  .gap-name {
    column-gap: 0.666em;
  }

  /* Inheritance Styling */
  .inherited {
    color: #999;
    border-left: 5px solid #eee;
    padding-left: 1em;
  }
  .inheritance em {
    font-style: normal;
    font-weight: bold;
  }

  /* Docstring Formatting */
  .desc h2 {
    font-weight: 400;
    font-size: 1.25em;
  }
  .desc h3 {
    font-size: 1.2em;
  }
  .desc dt code {
    background: inherit;
  }
  div.desc {
    margin-left: 2em;
  }
  div.desc:has(> p) {
    margin-bottom: 4rem;
  }

  /* Source Code Display */
  .source > summary,
  .git-link-div {
    color: #666;
    text-align: right;
    font-weight: 400;
    font-size: 0.8em;
    text-transform: uppercase;
  }
  .source summary > * {
    white-space: nowrap;
    cursor: pointer;
  }
  .git-link {
    color: inherit;
    margin-left: 1em;
  }
  section > pre:has(code) {
    border-radius: 10px;
    border: 0;
    margin-bottom: 3em;
  }
  section > pre code {
    font-size: 13.5px;
  }
  details.source div.rounded {
    background: #231e18;
    padding: 1em;
    border-radius: 10px;
    margin: 0 0 3em 0;
  }
  details.source div.rounded pre {
    margin: 0;
  }
  details.source pre {
    max-height: 500px;
    overflow: auto;
    border: 0;
    scrollbar-color: #484040 #231e18;
  }
  details.source pre::-webkit-scrollbar,
  details.source pre::-webkit-scrollbar-corner {
    background: #231e18;
    width: 8px;
    height: 8px;
  }
  details.source pre::-webkit-scrollbar-thumb {
    background: #484040;
    border-radius: 8px;
  }
  details.source pre code {
    font-size: 13.5px;
    overflow: visible;
    min-width: max-content;
  }
  details.source pre code.hljs {
    padding: 1.75em 0.75em;
  }
  div.desc div.mermaid > svg {
    background-color: inherited;
  }

  /* Horizontal Lists */
  .hlist {
    list-style: none;
  }
  .hlist li {
    display: inline;
  }
  .hlist li:after {
    content: ',\2002';
  }
  .hlist li:last-child:after {
    content: none;
  }
  .hlist .hlist {
    display: inline;
    padding-left: 1em;
  }

  /* Misc Elements */
  img {
    max-width: 100%;
  }
  td {
    padding: 0 0.5em;
  }

  /* Admonition Styles */
  .admonition {
    padding: 0.1em 1em;
    margin: 1em 0;
  }
  .admonition-title {
    font-weight: bold;
  }
  .admonition.note,
  .admonition.info,
  .admonition.important {
    background: #aef;
  }
  .admonition.todo,
  .admonition.versionadded,
  .admonition.tip,
  .admonition.hint {
    background: #dfd;
  }
  .admonition.warning,
  .admonition.versionchanged,
  .admonition.deprecated {
    background: #fd4;
  }
  .admonition.error,
  .admonition.danger,
  .admonition.caution {
    background: lightpink;
  }
</%def>

<%def name="desktop()" filter="minify_css">
  @media screen and (min-width: 700px) {
    #sidebar {
      width: 30%;
      height: 100vh;
      overflow: auto;
      position: sticky;
      top: 0;
      background: rgba(255, 255, 255, 0.8);
      backdrop-filter: blur(10px);
      border-right: 1px solid rgba(0, 0, 0, 0.05);
    }
    #module-list {
      width: 100%;
      max-width: 1100px;
      padding: 4em;
      margin: 0 auto;
    }
    #content {
      width: 70%;
      max-width: 120ch;
      padding: 3em 4em;
      border-left: 1px solid rgba(0, 0, 0, 0.05);
    }
    pre code {
      font-size: 1em;
    }
    .name {
      font-size: 1em;
    }
    main {
      display: flex;
      flex-direction: row-reverse;
      justify-content: flex-end;
    }
    .toc ul ul,
    #index ul ul {
      padding-left: 1em;
    }
    .toc > ul > li {
      margin-top: 0.5em;
    }
    #http-server-module-list {
      grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
    }
    #index h3 {
      padding: 8px 0;
      border: none;
      margin-top: 1.5em;
    }
  }
  @media screen and (min-width: 1000px) {
    #sidebar {
      width: 25%;
    }
    #content {
      width: 75%;
    }
  }
</%def>

<%def name="print()" filter="minify_css">
@media print {
  #sidebar h1 {
    page-break-before: always;
  }
  .source {
    display: none;
  }

  * {
    background: transparent !important;
    color: #000 !important;
    box-shadow: none !important;
    text-shadow: none !important;
  }

  a[href]:after {
    content: " (" attr(href) ")";
    font-size: 90%;
  }

  a[href][title]:after {
    content: none;
  }
  abbr[title]:after {
    content: " (" attr(title) ")";
  }

  .ir a:after,
  a[href^="javascript:"]:after,
  a[href^="#"]:after {
    content: "";
  }

  pre,
  blockquote {
    border: 1px solid #999;
    page-break-inside: avoid;
  }

  thead {
    display: table-header-group;
  }
  tr, img {
    page-break-inside: avoid;
  }
  img {
    max-width: 100% !important;
  }

  @page {
    margin: 0.5cm;
  }

  p, h2, h3 {
    orphans: 3;
    widows: 3;
  }

  h1, h2, h3, h4, h5, h6 {
    page-break-after: avoid;
  }
}
</%def>
