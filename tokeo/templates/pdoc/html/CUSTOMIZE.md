# Customizing the tokeo pdoc documentation

Everything here can be changed from a derived project without touching tokeo.
Three layers, in increasing order of effort:

| Layer | Changes | Rebuild needed |
|---|---|---|
| [Config](#1-config-only) | Title, brand, favicon, language, what is rendered | no |
| [Theme CSS](#2-theme-css) | Colours, fonts, component styling | no |
| [Templates](#3-templates) | Markup, layout, added pages | no |
| [Tailwind](#4-rebuilding-tailwind) | New utility classes outside the reserve | yes |

---

## 1. Config only

Set in the `[pdoc]` section of the app config, e.g. `config/<app>/base.d/pdoc.yaml`:

```yaml
pdoc:
  title: "Spiral Docs"                 # suffix in <title>: "module · Spiral Docs"
  brand: "🍒 Spiral"                   # sidebar label; null => "🚀 <app label>"
  favicon: public/spiral-favicon.ico   # target of <link rel="icon">
  html_lang: de                        # lang attribute of <html>
  show_config: true                    # render the yaml config page
  ancestors_max_depth: 2               # class hierarchy levels in the docs
```

These win over the tokeo defaults. Without `brand` the sidebar falls back to
the app label, so the app name alone is often enough.

See `config/pdoc.yaml` in the tokeo scaffold for every key with its default.

---

## 2. Theme CSS

`assets/tokeo.theme.css` is plain, non-minified CSS, loaded at runtime. Edit
and reload — no build step.

### Brand colours

All component styles reference these tokens, so changing one recolours
everything at once:

```css
:root {
  --color-brand-highlight: #ffee99;   /* target/anchor highlight */
  --color-brand-primary:   #3a7ab9;   /* links, section headings */
  --color-brand-secondary: #2a5885;   /* secondary headings */
  --color-brand-accent:    #d150e2;   /* hover state */
  --color-brand-ident:     #a12d8a;   /* identifiers in signature boxes */
  --color-brand-ink:       #333333;   /* body text */
  --color-brand-muted:     #666666;   /* labels, meta text */
  --gradient-brand: linear-gradient(135deg, #ff68dc 0%, #3a6073 100%);
}
```

The same tokens back the `brand-*` Tailwind utilities (`text-brand-primary`,
`bg-brand-accent`, …), so template classes follow along automatically.

### Component classes

| Class | Applies to |
|---|---|
| `.tk-header` | The brand box at the top of the sidebar |
| `.tk-doc` | Any rendered markdown block (headings, lists, tables, code) |
| `.tk-member-doc` | The indented documentation under a class member |
| `.tk-sig` | Function and method signatures |
| `.tk-chev` | The expand chevron in the sidebar |
| `.admonition`, `.admonition-title` | `!!! note` / `.. note::` boxes |
| `.pdoc-copy` | The copy button on code blocks |

Override or extend with ordinary CSS:

```css
.tk-doc h2 { margin-top: 3rem; }
.tk-doc pre { border-radius: 0; }
```

### Fonts

`@font-face` blocks near the top of the file point at `assets/fonts/`. To swap
a font, replace the woff2 files and adjust the `src:` URLs, or override
`--font-sans` / `--font-mono` in `:root`.

---

## 3. Templates

### How overriding works

List your own template package **before** tokeo's:

```yaml
pdoc:
  templates:
    - spiral.templates.pdoc.html   # wins
    - tokeo.templates.pdoc.html    # fallback
```

The resulting search path is `[your packages…, tokeo, pdoc defaults]`. Supply
**only the files you want to change**; tokeo provides the rest, and pdoc's own
templates stay behind both so they never leak into the layout.

The same mechanism exists for the decorator snippets:

```yaml
pdoc:
  docstrings:
    - spiral.templates.pdoc.docstrings   # own/overriding snippets
    - tokeo.templates.pdoc.docstrings    # fallback
```

### Overridable files

| File | Contents |
|---|---|
| `frame.html.jinja2` | `<!doctype>`, `<html lang>`, `<head>` skeleton, `<body>` |
| `head.html` | Highlighting, mermaid, copy button, extra CSS/JS |
| `module.html.jinja2` | Module page: sidebar plus content |
| `index.html.jinja2` | Landing page listing the root packages |
| `config.html.jinja2` | The yaml config page |
| `assets/tokeo.theme.css` | Editable theme (see above) |
| `assets/tailwind.min.css` | Pre-built framework (see below) |

`assets/` is copied into the output directory on every render, with later
template directories overwriting earlier ones — so an asset of the same name
in your own package replaces tokeo's.

### Frame blocks

The page templates extend `frame.html.jinja2` and fill these blocks:

```jinja
{% extends "frame.html.jinja2" %}

{% block title %}…{% endblock %}    {# contents of <title> #}
{% block favicon %}…{% endblock %}  {# defaults to the configured favicon #}
{% block head %}…{% endblock %}     {# scripts, meta; tokeo includes head.html #}
{% block style %}…{% endblock %}    {# stylesheet links #}
{% block body %}…{% endblock %}     {# the whole page #}
```

tokeo ships its own frame because pdoc hardcodes `<html lang="en">` outside of
any block. Replacing only `head.html` while keeping tokeo's frame, module and
index templates is the common case:

```
spiral/templates/pdoc/html/
└── head.html          # everything else comes from tokeo
```

### Template globals

Available in every template, on top of pdoc's own (`module`, `submodules`, …):

| Global | Type | Meaning |
|---|---|---|
| `app` | object | The cement app instance |
| `app_name` | str | App label |
| `doc_title` | str | Configured `title` |
| `brand` | str | Configured `brand`, empty when unset |
| `html_lang` | str | Configured `html_lang` |
| `has_config` | bool | Whether a config page will be written |
| `tokeo_version`, `pdoc_version` | str | For the footer credit |
| `decorators(func)` | callable | Decorator metadata for a function |
| `ancestors(cls, depth)` | callable | MRO above a class, capped |
| `ancestors_max_depth` | int | Configured cap |
| `subclasses(cls)` | callable | Loaded direct subclasses, sorted |
| `own_init(cls)` | callable | The class's own `__init__`, or None |
| `is_namespace(mod)` | callable | True for PEP 420 namespace packages |
| `is_enum(cls)` | callable | True for `enum.Enum` subclasses |
| `all_modules` | set | Names of every documented module |
| `configdict`, `configsettings`, `configenvs` | — | Config page data |
| `redact_data(data)` | callable | Replaces every value, keeps the structure |
| `math`, `mermaid` | bool | Both False: pdoc's own includes target markup this layout does not use; `head.html` wires up mermaid itself |

### Adding your own page

Templates rendered by pdoc are driven by the module walk, so an extra page
needs code as well as a template. The pattern tokeo uses for the config page:

```python
def spiral_pdoc_extra_page(app):
    import pdoc.render
    from pathlib import Path

    pdoc.render.env.globals['my_data'] = collect_something()
    html = pdoc.render.env.get_template('extra.html.jinja2').render()
    out = Path(app.pdoc._output_dir) / 'extra' / 'index.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding='utf-8')

app.hook.register('tokeo_pdoc_post_render', spiral_pdoc_extra_page)
```

Put `extra.html.jinja2` in your template package and let it extend
`frame.html.jinja2`. Link to it from an overridden `module.html.jinja2` or
`index.html.jinja2`.

Two hooks bracket the render: `tokeo_pdoc_pre_render` runs before the module
walk (dramatiq uses it to swap its actor decorator for a documentable stub),
`tokeo_pdoc_post_render` after everything is written.

### Documenting your own decorators

Register a handler and point it at a markdown snippet:

```python
def my_pdoc_render_decorator(app, func, decorator, args, kwargs):
    if decorator == '@app.my.thing':
        return dict(
            decorator=decorator,
            params=None,
            docstring=app.pdoc.docstrings('decorator', 'my.thing'),
        )

app.hook.register('tokeo_pdoc_render_decorator', my_pdoc_render_decorator)
```

The snippet lives at `templates/pdoc/docstrings/decorator/my.thing.md`. It is
written once and appended to every function carrying that decorator.

---

## 4. Rebuilding Tailwind

`assets/tailwind.min.css` is a fixed, pre-built file (~630 KB, ≈72 KB gzipped,
browser-cached). Treat it like a CDN drop-in and never hand-edit it.

Besides the classes the templates use, it reserves a broad set of common
utilities, so most name-based tweaks work **without** a rebuild.

### Available without a rebuild

- **layout**: display, position, float, `sr-only`
- **flex & grid**: `flex-row/col/wrap`, `items-/justify-/self-/content-*`,
  `grid-cols-1…12`, `col-span-1…12`, `grid-rows-1…6`, `order-*`
- **spacing**: `m/p{,x,y,t,r,b,l}` and `gap`, scale 0…40 (48/56/64 for m/p),
  plus `auto` and `px`; `space-{x,y}-*`
- **sizing**: `w`/`h` full scale plus `full/screen/min/max/fit` and common
  fractions; `min-w`, `min-h`, `max-w` (incl. `xs…7xl`, `prose`, `screen-*`),
  `max-h`
- **typography**: `text-xs…9xl`, `font-thin…black`, `font-sans/serif/mono`,
  `leading-*`, `tracking-*`, alignment, `underline`, case, `truncate`,
  `break-*`, `whitespace-*`, `align-*`, `list-*`, `italic`
- **colours**: the full default palette (slate…rose, 50–950) plus `brand-*`,
  for `text-`/`bg-`/`border-`, base and the variants
  `hover: focus: active: group-hover: disabled: dark:`
- **borders**: `border{,-0,-2,-4,-8}`, per side, styles, all `rounded-*`
- **effects**: `shadow-*`, `opacity-*`, `transition*`, `duration-*`, `ease-*`,
  `scale-*`, small `translate-*`
- **misc**: offsets 0–4, `z-*`, `overflow-*`, `cursor-*`, `select-*`,
  `object-*`
- **responsive** `sm: md: lg: xl: 2xl:` for layout/spacing/sizing/typography/grid
- **structural** `first: last: odd: even:` for rows and lists

### Needs a rebuild

- **Arbitrary values in brackets**: `mt-[7px]`, `text-[13px]`, `w-[42%]`,
  `bg-[#123456]`, `grid-cols-[1fr_auto]`. Only the bracket values already used
  in the shipped templates are baked in.
- **Values outside the reserved scales**: `m-52`, `p-72`, an unlisted shade.
- **Exotic families**: `ring-*`, `outline-*`, `blur-*` and other filters,
  `backdrop-*`, `animate-*`, `aspect-*`, `columns-*`, `place-*`, `accent-*`,
  `caret-*`, gradients (`from-/via-/to-`).

In practice, prefer a reserved class name or plain CSS in `tokeo.theme.css`.

### Build setup

The build tooling is development-only and not part of the installable package.
Create a `pdoc-theme/` folder next to your package with these two files.

**`pdoc-theme/package.json`**

```json
{
  "name": "tokeo-pdoc-theme",
  "private": true,
  "description": "Development-only Tailwind build for the pdoc documentation theme. Not shipped in the Python package.",
  "scripts": {
    "build:framework": "tailwindcss -i tailwind.min.src.css -o ../tokeo/templates/pdoc/html/assets/tailwind.min.css --minify",
    "watch:framework": "tailwindcss -i tailwind.min.src.css -o ../tokeo/templates/pdoc/html/assets/tailwind.min.css --watch"
  },
  "devDependencies": {
    "@fontsource-variable/inter": "^5.0.0",
    "@fontsource-variable/source-code-pro": "^5.0.0",
    "@tailwindcss/cli": "^4.0.0",
    "tailwindcss": "^4.0.0"
  }
}
```

Adjust the two output paths if your package is not called `tokeo`.

**`pdoc-theme/tailwind.min.src.css`**

The `@source` globs must point at the real template files — if they do not
match, only the `@source inline(...)` reserve ends up in the output and every
bracket value used in the templates silently stops working. Verify after a
rebuild that a known bracket class such as `.pl-\[2\.4rem\]` is still present.

```css
/* ============================================================================
 * tailwind.min.src.css — source for the pre-built tailwind.min.css.
 * Besides the classes actually used in the templates, it reserves a broad set
 * of common utilities so name-based tweaks in tokeo.theme.css / templates work
 * WITHOUT rebuilding. Regenerate with:
 *   tailwindcss -i tailwind.min.src.css -o ../tokeo/templates/pdoc/html/assets/tailwind.min.css --minify
 * NOT reserved (still need a rebuild if introduced): arbitrary values in
 * square brackets (e.g. mt-[7px], text-[13px], bg-[#123]) and anything outside
 * the scales listed below.
 * ========================================================================== */
@import "tailwindcss";
@source "../tokeo/templates/pdoc/html/frame.html.jinja2";
@source "../tokeo/templates/pdoc/html/module.html.jinja2";
@source "../tokeo/templates/pdoc/html/index.html.jinja2";
@source "../tokeo/templates/pdoc/html/config.html.jinja2";
@source "../tokeo/templates/pdoc/html/head.html";

@theme {
  --color-brand-highlight: #ffee99;
  --color-brand-primary: #3a7ab9;
  --color-brand-secondary: #2a5885;
  --color-brand-accent: #d150e2;
  --color-brand-ident: #a12d8a;
  --color-brand-ink: #333333;
  --color-brand-muted: #666666;
  --font-sans: "Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: "Source Code Pro", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}

/* ---- display / box ---- */
@source inline("{,sm:,md:,lg:,xl:,2xl:}{block,inline-block,inline,flex,inline-flex,grid,inline-grid,hidden,contents,table,table-cell,table-row,flow-root}");
@source inline("box-{border,content}");
@source inline("{,md:,lg:,xl:,2xl:}{static,relative,absolute,fixed,sticky}");
@source inline("float-{left,right,none}");
@source inline("{,md:,lg:,xl:,2xl:}{sr-only,not-sr-only}");

/* ---- flex / grid ---- */
@source inline("{,md:,lg:,xl:,2xl:}{flex-row,flex-row-reverse,flex-col,flex-col-reverse,flex-wrap,flex-nowrap,flex-1,flex-auto,flex-initial,flex-none,grow,grow-0,shrink,shrink-0}");
@source inline("{,md:,lg:,xl:,2xl:}items-{start,center,end,baseline,stretch}");
@source inline("{,md:,lg:,xl:,2xl:}justify-{start,center,end,between,around,evenly}");
@source inline("{,md:,lg:,xl:,2xl:}self-{auto,start,center,end,stretch,baseline}");
@source inline("{,md:,lg:,xl:,2xl:}content-{start,center,end,between,around,evenly}");
@source inline("{,md:,lg:,xl:,2xl:}grid-cols-{1,2,3,4,5,6,7,8,9,10,11,12,none}");
@source inline("{,md:,lg:,xl:,2xl:}col-span-{1,2,3,4,5,6,7,8,9,10,11,12,full}");
@source inline("{,md:,lg:,xl:,2xl:}grid-rows-{1,2,3,4,5,6}");
@source inline("{,md:,lg:,xl:,2xl:}order-{1,2,3,4,5,6,7,8,9,10,11,12,first,last,none}");

/* ---- spacing ---- */
@source inline("{,md:,lg:,xl:,2xl:}{m,mx,my,mt,mr,mb,ml,p,px,py,pt,pr,pb,pl}-{0,0.5,1,1.5,2,2.5,3,3.5,4,5,6,7,8,9,10,11,12,14,16,20,24,28,32,36,40,48,56,64,px}");
@source inline("{,md:,lg:,xl:,2xl:}{m,mx,my,mt,mr,mb,ml}-auto");
@source inline("{,md:,lg:,xl:,2xl:}gap-{0,0.5,1,1.5,2,2.5,3,3.5,4,5,6,7,8,10,12,16,20,24}");
@source inline("{,md:,lg:,xl:,2xl:}gap-{x,y}-{0,1,2,3,4,5,6,8,10,12}");
@source inline("space-{x,y}-{0,0.5,1,1.5,2,2.5,3,3.5,4,5,6,8,10}");

/* ---- sizing ---- */
@source inline("{,md:,lg:,xl:,2xl:}w-{0,0.5,1,1.5,2,2.5,3,4,5,6,7,8,9,10,11,12,14,16,20,24,28,32,36,40,48,56,64,72,80,96,px,auto,full,screen,svw,min,max,fit,1/2,1/3,2/3,1/4,3/4,1/5,2/5,3/5,4/5}");
@source inline("{,md:,lg:,xl:,2xl:}h-{0,0.5,1,1.5,2,2.5,3,4,5,6,7,8,9,10,11,12,14,16,20,24,28,32,36,40,48,56,64,72,80,96,px,auto,full,screen,svh,min,max,fit}");
@source inline("min-w-{0,full,min,max,fit}");
@source inline("min-h-{0,full,screen,svh,min,max,fit}");
@source inline("max-w-{0,none,xs,sm,md,lg,xl,2xl,3xl,4xl,5xl,6xl,7xl,full,min,max,fit,prose,screen-sm,screen-md,screen-lg,screen-xl,screen-2xl}");
@source inline("max-h-{0,none,full,screen,svh,px,4,8,12,16,20,24,32,40,48,56,64,80,96}");

/* ---- typography ---- */
@source inline("{,md:,lg:,xl:,2xl:}text-{xs,sm,base,lg,xl,2xl,3xl,4xl,5xl,6xl,7xl,8xl,9xl}");
@source inline("font-{thin,extralight,light,normal,medium,semibold,bold,extrabold,black}");
@source inline("font-{sans,serif,mono}");
@source inline("{italic,not-italic,antialiased,subpixel-antialiased}");
@source inline("leading-{none,tight,snug,normal,relaxed,loose,3,4,5,6,7,8,9,10}");
@source inline("tracking-{tighter,tight,normal,wide,wider,widest}");
@source inline("{,md:,lg:,xl:,2xl:}text-{left,center,right,justify,start,end}");
@source inline("{underline,overline,line-through,no-underline}");
@source inline("decoration-{0,1,2,4,8,auto,from-font}");
@source inline("{uppercase,lowercase,capitalize,normal-case}");
@source inline("{truncate,text-ellipsis,text-clip}");
@source inline("break-{normal,words,all,keep}");
@source inline("whitespace-{normal,nowrap,pre,pre-line,pre-wrap,break-spaces}");
@source inline("align-{baseline,top,middle,bottom,text-top,text-bottom,sub,super}");
@source inline("list-{none,disc,decimal,inside,outside}");
@source inline("indent-{0,1,2,4,8}");

/* ---- colours (full default palette + brand) for text/bg/border, base + hover ---- */
@source inline("{,hover:,focus:,active:,group-hover:,disabled:,dark:}{text,bg,border}-{inherit,current,transparent,black,white}");
@source inline("{,hover:,focus:,active:,group-hover:,disabled:,dark:}{text,bg,border}-{slate,gray,zinc,neutral,stone,red,orange,amber,yellow,lime,green,emerald,teal,cyan,sky,blue,indigo,violet,purple,fuchsia,pink,rose}-{50,100,200,300,400,500,600,700,800,900,950}");
@source inline("{,hover:,focus:,active:,group-hover:,disabled:,dark:}{text,bg,border}-brand-{highlight,primary,secondary,accent,ident,ink,muted}");

/* ---- borders / radius ---- */
@source inline("{border,border-0,border-2,border-4,border-8}");
@source inline("border-{t,r,b,l,x,y}");
@source inline("border-{t,r,b,l}-{0,2,4,8}");
@source inline("border-{solid,dashed,dotted,double,none}");
@source inline("{,md:,lg:,xl:,2xl:}rounded-{none,sm,md,lg,xl,2xl,3xl,full}");
@source inline("rounded-{t,r,b,l,tl,tr,br,bl}-{none,sm,md,lg,xl,2xl,3xl,full}");
@source inline("divide-{x,y}-{0,2,4}");

/* ---- effects / transitions ---- */
@source inline("{,hover:}shadow-{sm,md,lg,xl,2xl,inner,none}");
@source inline("opacity-{0,5,10,20,25,30,40,50,60,70,75,80,90,95,100}");
@source inline("{,hover:}opacity-{0,25,50,75,100}");
@source inline("transition{,-all,-colors,-opacity,-shadow,-transform,-none}");
@source inline("duration-{75,100,150,200,300,500,700,1000}");
@source inline("ease-{linear,in,out,in-out}");
@source inline("{,hover:}scale-{95,100,105,110}");
@source inline("{,hover:}{translate-x,translate-y}-{0,0.5,1,2}");

/* ---- position offsets / z ---- */
@source inline("{top,right,bottom,left,inset}-{0,0.5,1,2,3,4,auto,full,px}");
@source inline("z-{0,10,20,30,40,50,auto}");

/* ---- overflow / misc ---- */
@source inline("overflow-{auto,hidden,visible,scroll,clip}");
@source inline("overflow-{x,y}-{auto,hidden,visible,scroll,clip}");
@source inline("cursor-{pointer,default,not-allowed,wait,text,move,help,auto}");
@source inline("select-{none,text,all,auto}");
@source inline("object-{contain,cover,fill,none,scale-down}");

/* ---- structural variants (first/last/odd/even) for rows & lists ---- */
@source inline("{first:,last:,odd:,even:}{bg,text,border}-{slate,gray,zinc,red,orange,amber,yellow,lime,green,emerald,teal,cyan,sky,blue,indigo,violet,purple,pink,rose}-{50,100,200,900}");
@source inline("{first:,last:,odd:,even:}{bg,text,border}-brand-{highlight,primary,secondary,accent,ident,muted}");
@source inline("{first:,last:,odd:,even:}{mt,mb,pt,pb,pl,pr,border-t,border-b}-{0,1,2,3,4}");
@source inline("{first:,last:,odd:,even:}{rounded-t,rounded-b,rounded}-{none,sm,md,lg,xl}");
@source inline("{first:,last:,odd:,even:}{border,border-0,border-t,border-b}");
```

Extend the `@source inline(...)` reserve there to make a class permanently
available without a rebuild.

### Build commands

```bash
cd pdoc-theme
npm install
npm run build:framework      # one-off, minified
npm run watch:framework      # rebuild on template changes
```

### Refreshing the bundled fonts

The woff2 files under `assets/fonts/` come from the Fontsource packages (latin
subset, variable weight axis). Run from `pdoc-theme/` after `npm install`:

```bash
cp node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2 \
   ../tokeo/templates/pdoc/html/assets/fonts/Inter-latin-wght-normal.woff2
cp node_modules/@fontsource-variable/inter/files/inter-latin-wght-italic.woff2 \
   ../tokeo/templates/pdoc/html/assets/fonts/Inter-latin-wght-italic.woff2
cp node_modules/@fontsource-variable/source-code-pro/files/source-code-pro-latin-wght-normal.woff2 \
   ../tokeo/templates/pdoc/html/assets/fonts/SourceCodePro-latin-wght-normal.woff2
```

Keep the `*-OFL.txt` licence files next to the fonts — both are SIL Open Font
License 1.1.

---

## Working on the theme

`pdoc render --serve --watch` re-renders whenever a watched file changes. Note
that the template directories are **not** watched by default, since
`watch_includes` covers `*.py, *.yaml, *.yml, *.md`. While editing templates,
add the suffixes and the directory:

```yaml
pdoc:
  watch_includes: '*.py, *.yaml, *.yml, *.md, *.jinja2, *.html, *.css'
```

Watching the template directory itself additionally requires a line in
`_watch_dirs()`; without it only module and config changes trigger a rebuild.

The theme is an original Tailwind (MIT) composition against the tokeo brand
tokens. No upstream pdoc or pdoc3 CSS is used.
