# Check that a URL answers with HTTP 2xx/3xx. Usage (job arguments): <url>
set -euo pipefail
url="${1:?pass the URL as the first argument}"
code=$(python3 -c 'import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=10).status)' "$url")
echo "$url -> HTTP $code"
