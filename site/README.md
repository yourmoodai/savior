# Homepage

The public site is a Zola build of Markdown, HTML templates, and CSS. Python 3.11+
is the only prerequisite for the launcher. It downloads Zola 0.23.6 during first
setup, verifies the pinned archive checksum, and reuses the cache thereafter.
No application build or model download is needed.

Zola keeps content in Markdown and layouts in HTML templates while producing
plain files for GitHub Pages. CSS scroll timelines provide progressive parallax;
unsupported browsers and reduced-motion preferences receive the static layout.
This avoids a browser runtime and keeps the landing page independent of the
native mod's release pipeline.

```sh
python3 scripts/site.py serve
python3 scripts/site.py build
python3 scripts/site.py check
```

Preview listens on `127.0.0.1:1111`. Zola’s development server uses live reload;
the production artifact in `site/public` contains no JavaScript. Use a plain
static server to inspect the production artifact. Both build and check validate
local links, anchors, and the absence of active or external resources.

For browser regression checks, install the repository development dependencies
with `npm ci`, install the test browser with `npx playwright install chromium`,
and run `npm run site:test`. This checks JavaScript-disabled reading, reduced
motion, narrow layouts, local resource requests, and scroll-driven transforms.
Screenshots are saved under `.cache/site/`.

`zola.toml` owns release status and installation wording. The launcher blocks a
released state until a separate assurance decision changes that gate. The public
assurance page records the current interception boundary. The existing
installed-release gate exercises both replacement strategies and failure cases;
it does not establish full host egress coverage.
The current install snippet describes a future single-adapter release. Review
it alongside ADR 2's client-version selection contract before publishing an
installer or advertising support for another Claude API.

## Publication

The `site` workflow checks pull requests and publishes merged `main` builds via
Pages artifacts. Enable GitHub Actions as the Pages source, verify ownership of
`yourmood.ai`, and configure `savior.yourmood.ai` in Pages before creating the DNS
CNAME from `savior` to `yourmoodai.github.io`. Enable HTTPS when its certificate
is ready. Actions publishing does not require a repository `CNAME` file.

Check mobile and desktop layouts, keyboard focus, 200% zoom, reduced motion, and
the static fallback before publication. Confirm the persistent unreleased label
and installation notice remain readable. The page must make no third-party
resource requests. Local inference and whole-host privacy are distinct claims.
