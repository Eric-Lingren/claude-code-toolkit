#!/bin/bash
# PreToolUse hook: blocks common DATA-EXFILTRATION command shapes.
#
# Intent: allow normal internet use (plain `curl`/`wget` GET, API calls with
# inline data) while blocking commands that read a LOCAL file OUT to the network
# (uploads, POST-from-file, raw sockets, scp/rsync-to-remote, /dev/tcp, DNS/gist
# tricks, interpreter network one-liners).
#
# This is a BACKSTOP, not a complete control. A denylist cannot win against
# encodings, novel tools, or cloud CLIs (aws s3, gcloud, etc.). It raises the
# bar; it does not close the door. The only complete egress control is at the
# network layer.
#
# Exits 2 to block, 0 to allow.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# Flatten newlines/tabs so multi-line commands still match. Keep $COMMAND
# (original) for the message shown to the user.
CMD=$(printf '%s' "$COMMAND" | tr '\n\t' '  ')

block() {
  echo "Blocked: data-exfiltration pattern detected ($1)." >&2
  echo "" >&2
  echo "If this is intentional, run it yourself in a terminal:" >&2
  echo "" >&2
  echo "  $COMMAND" >&2
  exit 2
}

# 1. curl/wget shipping a LOCAL FILE out (the @file body, multipart upload, or
#    upload flags). Plain GETs and inline `-d '{...}'` API calls are NOT matched.
if printf '%s' "$CMD" | grep -iqE '(^|[[:space:];&|(])(curl|wget)[[:space:]]'; then
  # request body sourced from a file: -d @file, --data-binary @file, etc.
  printf '%s' "$CMD" | grep -iqE -- "(-d|--data|--data-binary|--data-ascii|--data-urlencode|--data-raw)[[:space:]]*['\"]?@" \
    && block "curl POST body read from a local file (@file)"
  # multipart form field sourced from a file: -F field=@file
  printf '%s' "$CMD" | grep -iqE -- "(-F|--form)[[:space:]]+[^[:space:]]*=@" \
    && block "curl multipart upload of a local file (-F field=@file)"
  # explicit upload flags
  printf '%s' "$CMD" | grep -iqE -- "(-T|--upload-file)[[:space:]]" \
    && block "curl file upload (-T/--upload-file)"
  # wget post/body flags
  printf '%s' "$CMD" | grep -iqE -- "(--post-file|--post-data|--body-file|--body-data)" \
    && block "wget post/body upload flag"
  # inlining file contents into the request via command substitution
  printf '%s' "$CMD" | grep -iqE '\$\([[:space:]]*(cat|head|tail|base64|xxd|openssl|gpg)\b|\$\([[:space:]]*<|`[[:space:]]*(cat|head|tail|base64)' \
    && block "curl/wget inlining file contents via \$(...) or backticks"
fi

# 2. Raw socket tools (rarely needed in normal dev; classic exfil channel)
printf '%s' "$CMD" | grep -iqE '(^|[[:space:];&|(])(nc|ncat|netcat|socat)[[:space:]]' \
  && block "raw socket tool (nc/ncat/netcat/socat)"

# 3. scp is always a remote copy; rsync only when a remote spec is present
printf '%s' "$CMD" | grep -iqE '(^|[[:space:];&|(])scp[[:space:]]' \
  && block "scp (remote file copy)"
printf '%s' "$CMD" | grep -iqE '(^|[[:space:];&|(])rsync[[:space:]].*([[:alnum:]_.-]+@[[:alnum:]_.-]+:|[[:alnum:]_.-]+::|[[:space:]][[:alnum:]_.-]+:)' \
  && block "rsync to a remote host"

# 4. ssh used as an exfil pipe: data piped IN, or stdin redirected from a file.
#    Interactive `ssh host` and `ssh host 'cmd'` are NOT matched.
printf '%s' "$CMD" | grep -iqE '\|[[:space:]]*ssh[[:space:]]' \
  && block "data piped into ssh"
printf '%s' "$CMD" | grep -iqE '(^|[[:space:];&|(])ssh[[:space:]].*<[[:space:]]*[^[:space:]&|]' \
  && block "ssh with stdin redirected from a file"

# 5. bash /dev/tcp and /dev/udp pseudo-device network channels
printf '%s' "$CMD" | grep -iqE '/dev/(tcp|udp)/' \
  && block "/dev/tcp or /dev/udp network channel"

# 6. DNS-based exfil: resolver querying a command-substituted / encoded name
printf '%s' "$CMD" | grep -iqE '(^|[[:space:];&|(])(dig|nslookup|host)[[:space:]].*(\$\(|`|base64)' \
  && block "DNS lookup of a command-substituted/encoded name"

# 7. Classic gist exfil
printf '%s' "$CMD" | grep -iqE '(^|[[:space:];&|(])gh[[:space:]]+gist[[:space:]]+create' \
  && block "gh gist create"

# 8. Interpreter one-liners that open a network connection. (These also bypass
#    command allowlists, so they matter most under blanket approval.)
printf '%s' "$CMD" | grep -iqE '(python[0-9]?|node|deno|ruby|perl|php)[[:space:]].*(-c|-e|--eval|--exec)[[:space:]].*(socket|urllib|requests|httplib|http\.client|net/http|Net::HTTP|fetch\(|net\.connect|https?://)' \
  && block "interpreter one-liner opening a network connection"

exit 0
