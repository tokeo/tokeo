<%
  import os

  import pdoc
  from pdoc.html_helpers import extract_toc, glimpse, to_html as _to_html, format_git_link

  from tokeo.core.utils.pdoc import DecoratedFunction

  def link(dobj: pdoc.Doc, name=None):
    name = name or dobj.qualname + ('()' if isinstance(dobj, pdoc.Function) else '')
    if isinstance(dobj, pdoc.External) and not external_links:
        return name
    url = dobj.url(relative_to=module, link_prefix=link_prefix,
                   top_ancestor=not show_inherited_members)
    return f'<a title="{dobj.refname}" href="{url}">{name}</a>'


  def to_html(text):
    return _to_html(text, docformat=docformat, module=module, link=link, latex_math=latex_math)


  def get_annotation(bound_method, sep=':'):
    annot = show_type_annotations and bound_method(link=link) or ''
    if annot:
        annot = ' ' + sep + '\N{NBSP}' + annot
    return annot
%>

<%def name="ident(name)"><span class="ident">${name}</span></%def>

<%def name="show_source(d)">
  % if (show_source_code or git_link_template) and \
        not isinstance(d, pdoc.Module) and d.source and \
        d.obj is not getattr(d.inherits, 'obj', None):
    <% git_link = format_git_link(git_link_template, d) %>
    % if show_source_code:
      <details class="source">
        <summary>
            <span>Expand source code</span>
            % if git_link:
              <a href="${git_link}" class="git-link" target="_blank">Browse git</a>
            %endif
        </summary>
        <div class="rounded"><pre><code class="python">${d.source | h}</code></pre></div>
      </details>
    % elif git_link:
      <div class="git-link-div"><a href="${git_link}" class="git-link">Browse git</a></div>
    %endif
  %endif
</%def>

<%def name="show_desc(d, short=False)">
  <%
  inherits = ' inherited' if d.inherits else ''
  docstring = glimpse(d.docstring) if short or inherits else d.docstring
  %>
  % if d.inherits:
      <p class="inheritance">
          <em>Inherited from:</em>
          % if hasattr(d.inherits, 'cls'):
              <code>${link(d.inherits.cls)}</code>.<code>${link(d.inherits, d.name)}</code>
          % else:
              <code>${link(d.inherits)}</code>
          % endif
      </p>
  % endif
  % if not isinstance(d, pdoc.Module):
  ${show_source(d)}
  % endif
  <div class="desc${inherits}">${docstring | to_html}</div>
</%def>

<%def name="show_module_list(modules)">
<h1>${html_title}</h1>

% if not modules:
  <p>No modules found.</p>
% else:
  <dl id="http-server-module-list">
  % for name, desc in modules:
      <div class="flex">
      <dt><a href="${link_prefix}${name}">${name}</a></dt>
      <dd>${desc | glimpse, to_html}</dd>
      </div>
  % endfor
  </dl>
% endif
</%def>

<%def name="show_column_list(items)">
  <%
      two_column = len(items) >= 6 and all(len(i.name) < 20 for i in items)
  %>
  <ul class="${'two-column' if two_column else ''}">
  % for item in items:
    <li><code>${link(item, item.name)}</code></li>
  % endfor
  </ul>
</%def>

<%def name="show_module(module)">
  <%
  variables = module.variables(sort=sort_identifiers)
  classes = module.classes(sort=sort_identifiers)
  functions = module.functions(sort=sort_identifiers)
  submodules = module.submodules()
  %>

  <%def name="show_func(f)">
    <dt id="${f.refname}"><code class="name flex flex-col">
        <%
            params = f.params(annotate=show_type_annotations, link=link)
            sep = ',<br>' if sum(map(len, params)) > 75 else ', '
            params = sep.join(params)
            return_type = get_annotation(f.return_annotation, '\N{non-breaking hyphen}>')
            decorated = DecoratedFunction(app, f, update_func_docstring=True, prepend_docstrings='\n\n###\n\n---\n\n###\n\n')
        %>
        % if decorated.has_decorators:
            % for decorator in decorated.decorators:
                <div>
                    <span class="decorator">${decorator['decorator']}</span><span>${f'({decorator["params"]})' if decorator["params"] is not None else ''}</span>
                </div>
            % endfor
        % endif
        <div>
            <span>${f.funcdef()} ${ident(f.name)}</span><span>(${params})${return_type}</span>
        </div>
    </code></dt>
    <dd>${show_desc(f)}</dd>
  </%def>

  <header>
  % if http_server:
    <nav class="http-server-breadcrumbs">
      <a href="/">All packages</a>
      <% parts = module.name.split('.')[:-1] %>
      % for i, m in enumerate(parts):
        <% parent = '.'.join(parts[:i+1]) %>
        :: <a href="/${parent.replace('.', '/')}/">${parent}</a>
      % endfor
    </nav>
  % endif
  <h1 class="title">${'Namespace' if module.is_namespace else  \
                      'Package' if module.is_package and not module.supermodule else \
                      'Module'} <code>${module.name}</code></h1>
  </header>

  <section id="section-intro">
  ${module.docstring | to_html}
  </section>

  <section>
    % if submodules:
    <h2 class="section-title" id="header-submodules">Sub-modules</h2>
    <dl>
    % for m in submodules:
      <dt><code class="name flex gap-name">${link(m)}</code></dt>
      <dd>${show_desc(m, short=True)}</dd>
    % endfor
    </dl>
    % endif
  </section>

  <section>
    % if variables:
    <h2 class="section-title" id="header-variables">Global variables</h2>
    <dl>
    % for v in variables:
      <% return_type = get_annotation(v.type_annotation) %>
      <dt id="${v.refname}"><code class="name flex gap-name">var ${ident(v.name)}${return_type}</code></dt>
      <dd>${show_desc(v)}</dd>
    % endfor
    </dl>
    % endif
  </section>

  <section>
    % if functions:
    <h2 class="section-title" id="header-functions">Functions</h2>
    <dl>
    % for f in functions:
      ${show_func(f)}
    % endfor
    </dl>
    % endif
  </section>

  <section>
    % if classes:
    <h2 class="section-title" id="header-classes">Classes</h2>
    <dl>
    % for c in classes:
      <%
      class_vars = c.class_variables(show_inherited_members, sort=sort_identifiers)
      smethods = c.functions(show_inherited_members, sort=sort_identifiers)
      inst_vars = c.instance_variables(show_inherited_members, sort=sort_identifiers)
      methods = c.methods(show_inherited_members, sort=sort_identifiers)
      mro = c.mro()
      subclasses = c.subclasses()
      params = c.params(annotate=show_type_annotations, link=link)
      sep = ',<br>' if sum(map(len, params)) > 75 else ', '
      params = sep.join(params)
      %>
      <dt id="${c.refname}"><code class="name class flex">
          <span>class ${ident(c.name)}</span>
          % if params:
              <span>(</span><span>${params})</span>
          % endif
      </code></dt>

      <dd>${show_desc(c)}

      % if mro:
          <h3>Ancestors</h3>
          <ul class="hlist">
          % for cls in mro:
              <li>${link(cls)}</li>
          % endfor
          </ul>
      %endif

      % if subclasses:
          <h3>Subclasses</h3>
          <ul class="hlist">
          % for sub in subclasses:
              <li>${link(sub)}</li>
          % endfor
          </ul>
      % endif
      % if class_vars:
          <h3>Class variables</h3>
          <dl>
          % for v in class_vars:
              <% return_type = get_annotation(v.type_annotation) %>
              <dt id="${v.refname}"><code class="name flex gap-name">var ${ident(v.name)}${return_type}</code></dt>
              <dd>${show_desc(v)}</dd>
          % endfor
          </dl>
      % endif
      % if smethods:
          <h3>Static methods</h3>
          <dl>
          % for f in smethods:
              ${show_func(f)}
          % endfor
          </dl>
      % endif
      % if inst_vars:
          <h3>Instance variables</h3>
          <dl>
          % for v in inst_vars:
              <% return_type = get_annotation(v.type_annotation) %>
              <dt id="${v.refname}"><code class="name flex gap-name">${v.kind} ${ident(v.name)}${return_type}</code></dt>
              <dd>${show_desc(v)}</dd>
          % endfor
          </dl>
      % endif
      % if methods:
          <h3>Methods</h3>
          <dl>
          % for f in methods:
              ${show_func(f)}
          % endfor
          </dl>
      % endif

      % if not show_inherited_members:
          <%
              members = c.inherited_members()
          %>
          % if members:
              <h3>Inherited members</h3>
              <ul class="hlist">
              % for cls, mems in members:
                  <li><code><b>${link(cls)}</b></code>:
                      <ul class="hlist">
                          % for m in mems:
                              <li><code>${link(m, name=m.name)}</code></li>
                          % endfor
                      </ul>

                  </li>
              % endfor
              </ul>
          % endif
      % endif

      </dd>
    % endfor
    </dl>
    % endif
  </section>
</%def>

<%def name="module_index(module)">
  <%
  variables = module.variables(sort=sort_identifiers)
  classes = module.classes(sort=sort_identifiers)
  functions = module.functions(sort=sort_identifiers)
  submodules = module.submodules()
  supermodule = module.supermodule
  %>
  <nav id="sidebar">

    <%include file="logo.mako"/>

    % if google_search_query:
        <div class="gcse-search" style="height: 70px"
             data-as_oq="${' '.join(google_search_query.strip().split()) | h }"
             data-gaCategoryParameter="${module.refname | h}">
        </div>
    % endif

    % if lunr_search is not None:
      <%include file="_lunr_search.inc.mako"/>
    % endif

    ${extract_toc(module.docstring) if extract_module_toc_into_sidebar else ''}
    <ul id="index">
    <li><h3>Home</h3>
      <ul>
        <li><code><a title="Home" href="/">Home</a></code></li>
      </ul>
    </li>

    % if supermodule:
    <li><h3>Super-Module</h3>
      <ul>
        <li><code>${link(supermodule)}</code></li>
      </ul>
    </li>
    % endif

    % if submodules:
    <li><h3><a href="#header-submodules">Sub-modules</a></h3>
      <ul>
      % for m in submodules:
        <li><code>${link(m)}</code></li>
      % endfor
      </ul>
    </li>
    % endif

    % if variables:
    <li><h3><a href="#header-variables">Global variables</a></h3>
      ${show_column_list(variables)}
    </li>
    % endif

    % if functions:
    <li><h3><a href="#header-functions">Functions</a></h3>
      ${show_column_list(functions)}
    </li>
    % endif

    % if classes:
    <li><h3><a href="#header-classes">Classes</a></h3>
      <ul>
      % for c in classes:
        <li>
        <h4><code>${link(c)}</code></h4>
        <%
            members = c.functions(sort=sort_identifiers) + c.methods(sort=sort_identifiers)
            if list_class_variables_in_index:
                members += (c.instance_variables(sort=sort_identifiers) +
                            c.class_variables(sort=sort_identifiers))
            if not show_inherited_members:
                members = [i for i in members if not i.inherits]
            if sort_identifiers:
              members = sorted(members)
        %>
        % if members:
          ${show_column_list(members)}
        % endif
        </li>
      % endfor
      </ul>
    </li>
    % endif

    </ul>
  </nav>
</%def>

<!doctype html>
<html lang="${html_lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, minimum-scale=1">
  <meta name="generator" content="pdoc3 ${pdoc.__version__}">

<%
    module_list = 'modules' in context.keys()  # Whether we're showing module list in server mode
%>

  % if module_list:
    <title>Python module list</title>
    <meta name="description" content="A list of documented Python modules.">
  % else:
    <title>${module.name} API documentation</title>
    <meta name="description" content="${module.docstring | glimpse, trim, h}">
  % endif

  <link rel="stylesheet" href="/assets/sanitize.min.css">
  <link rel="stylesheet" href="/assets/typography.min.css">
  % if syntax_highlighting:
    <link rel="stylesheet" href="/assets/hljs/styles/${hljs_style}.min.css">
  %endif

  <%namespace name="css" file="css.mako" />
  <style>${css.mobile()}</style>
  <style media="screen and (min-width: 700px)">${css.desktop()}</style>
  <style media="print">${css.print()}</style>

  % if google_analytics:
    <script async src="https://www.googletagmanager.com/gtag/js?id=${google_analytics}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', '${google_analytics}');
    </script>
  % endif

  % if google_search_query:
    <link rel="preconnect" href="https://www.google.com">
    <script async src="https://cse.google.com/cse.js?cx=017837193012385208679:pey8ky8gdqw"></script>
    <style>
        .gsc-control-cse {padding:0 !important;margin-top:1em}
        body.gsc-overflow-hidden #sidebar {overflow: visible;}
    </style>
  % endif

  % if latex_math:
    <script type="text/x-mathjax-config">MathJax.Hub.Config({ tex2jax: { inlineMath: [ ['$','$'], ["\\(","\\)"] ], processEscapes: true } });</script>
    <script async src="https://cdnjs.cloudflare.com/ajax/libs/mathjax/2.7.7/latest.js?config=TeX-AMS_CHTML" integrity="sha256-kZafAc6mZvK3W3v1pHOcUix30OHQN6pU/NO2oFkqZVw=" crossorigin></script>
  % endif

  % if syntax_highlighting:
    <script defer src="/assets/highlight.min.js"></script>
  % endif
  % if mermaid_support:
    <script defer src="/assets/mermaid.min.js"></script>
  % endif
    <script>
      window.addEventListener('DOMContentLoaded', () => {
  % if mermaid_support:
        // First process mermaid blocks before highlighting
        document.querySelectorAll('pre code.language-mermaid').forEach(el => {
            // Remove the code block from highlight.js processing
            el.classList.remove('language-mermaid');
            el.classList.add('nohighlight');
            // Create a div for mermaid
            const mermaidDiv = document.createElement('div');
            mermaidDiv.className = 'mermaid';
            mermaidDiv.innerHTML = el.textContent;
            // Replace the code block with the mermaid div
            const pre = el.parentElement;
            pre.parentElement.replaceChild(mermaidDiv, pre);
        });
  % endif
  % if syntax_highlighting:
        hljs.configure({languages: ['accesslog', 'bash', 'c', 'cmake', 'cpp', 'css', 'diff', 'django', 'go', 'graphql', 'handlebars', 'ini', 'javascript', 'json', 'less', 'lua', 'makefile', 'markdown', 'mermaid', 'nginx', 'pgsql', 'php', 'plaintext', 'powershell', 'protobuf', 'python', 'python-repl', 'ruby', 'rust', 'scss', 'shell', 'sql', 'typescript', 'wasm', 'xml', 'yaml']});
        hljs.highlightAll();
  % endif
  % if mermaid_support:
        // Initialize mermaid
        mermaid.initialize({
            startOnLoad: true,
            theme: 'forest',
            securityLevel: 'loose'
        });
  % endif
    })
  </script>

  <%include file="head.mako"/>
</head>
<body>
<main>
  % if module_list:
    <article id="content">
      ${show_module_list(modules)}
    </article>
  % else:
    <article id="content">
      ${show_module(module)}
    </article>
    ${module_index(module)}
  % endif
</main>

<footer id="footer">
    <%include file="credits.mako"/>
    <p>Generated by <a href="https://github.com/tokeo/tokeo" title="Tokeo pdoc: Python API documentation generator"><cite>Tokeo pdoc</cite> ${pdoc.__version__}</a>.</p>
</footer>

% if http_server and module:  ## Auto-reload on file change in dev mode
    <script>
    setInterval(() =>
        fetch(window.location.href, {
            method: "HEAD",
            cache: "no-store",
            headers: {"If-None-Match": "${os.stat(module.obj.__file__).st_mtime}"},
        }).then(response => response.ok && window.location.reload()), 700);
    </script>
% endif
</body>
</html>
