FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Virtual display + fonts + Firefox (camoufox) runtime libs + curl for healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb xauth ca-certificates wget curl fonts-liberation \
        libgtk-3-0 libx11-xcb1 libasound2 libdbus-glib-1-2 libxt6 libxcomposite1 \
        libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Browser stack pinned to the set lowedepot runs against Akamai. camoufox
# floats its playwright pin, and the driver patch below asserts on 1.61.0's
# bundle. Bump these together, deliberately (see lowedepot's CLAUDE.md).
RUN pip install "camoufox[geoip]==0.4.11" playwright==1.61.0

# The Camoufox *browser* is pinned too: `camoufox fetch` grabs the newest
# release, and 156.0.1-beta.33 broke camoufox 0.4.11 (UnknownProperty
# appCodeName). Install it the way `camoufox fetch` would, then validate the
# config so a mismatched pair fails the build, not a scrape.
ARG CAMOUFOX_BROWSER=152.0.4-beta.28
RUN d=$(python -c "from camoufox.pkgman import INSTALL_DIR; print(INSTALL_DIR)") \
    && rm -rf "$d" && mkdir -p "$d" \
    && wget -qO /tmp/camoufox.zip "https://github.com/daijro/camoufox/releases/download/v${CAMOUFOX_BROWSER}/camoufox-${CAMOUFOX_BROWSER}-lin.x86_64.zip" \
    && python -m zipfile -e /tmp/camoufox.zip "$d" && rm /tmp/camoufox.zip \
    && printf '{"version":"%s","release":"%s"}' "${CAMOUFOX_BROWSER%%-*}" "${CAMOUFOX_BROWSER#*-}" > "$d/version.json" \
    && chmod -R 755 "$d" \
    && python -c "from camoufox.locale import download_mmdb; from camoufox.addons import DefaultAddons, maybe_download_addons; download_mmdb(); maybe_download_addons(list(DefaultAddons))" \
    && python -c "from camoufox.utils import launch_options; launch_options(headless=True)" \
    && echo "camoufox browser ${CAMOUFOX_BROWSER} installed + config validated"

# Camoufox's Firefox emits uncaught page-error events with a malformed
# `location` that crash the Playwright driver whenever the site throws a JS
# error. Neuter that handler and default the fields. Fails loudly if the patch
# stops matching.
RUN f=$(find /usr/local/lib -name coreBundle.js -path '*/playwright/driver/*' | head -1) \
    && sed -i -E \
        -e "s/_onUncaughtError\(([a-zA-Z0-9_]+)\) \{/_onUncaughtError(\1) { return;/g" \
        -e "s/(_onWebSocket[A-Za-z]*\([a-zA-Z0-9_]*\)) \{/\1 { return;/g" \
        -e "s/pageError\.location\.url/pageError.location?.url??''/g" \
        -e "s/pageError\.location\.lineNumber/pageError.location?.lineNumber??0/g" \
        -e "s/pageError\.location\.columnNumber/pageError.location?.columnNumber??0/g" \
        "$f" \
    && grep -Eq "_onUncaughtError\([a-zA-Z0-9_]+\) \{ return;" "$f" \
    && grep -Eq "_onWebSocketOpened\([a-zA-Z0-9_]*\) \{ return;" "$f" \
    && echo "camoufox driver patched OK"

COPY . /app
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
