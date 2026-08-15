# shellcheck shell=bash
# Source this file, then call: jira GET /rest/api/2/issue/PROJ-123
# Do not print JIRA_TOKEN. Do not run with set -x.

_atlassian_scripts_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

jira_python() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$@"
  elif command -v python >/dev/null 2>&1; then
    python "$@"
  else
    printf 'Need python3 or python on PATH.\n' >&2
    return 127
  fi
}

_jira_token_candidates() {
  if [[ -n "${JIRA_TOKEN_FILE:-}" ]]; then
    printf '%s\n' "$JIRA_TOKEN_FILE"
    return 0
  fi
  printf '%s\n' "${HOME}/.config/atlassian/jira.token"
  if [[ -n "${USERPROFILE:-}" ]]; then
    local up="${USERPROFILE//\\//}"
    printf '%s\n' "${up}/.config/atlassian/jira.token"
  fi
}

jira_url() {
  local base="${JIRA_BASE:-${JIRA_BASE_URL:-}}"
  local path="$1"
  if [[ "$path" == http://* || "$path" == https://* ]]; then
    printf '%s\n' "$path"
    return 0
  fi
  base="${base%/}"
  [[ "$path" == /* ]] || path="/$path"
  printf '%s%s\n' "$base" "$path"
}

parse_issue_key() {
  local raw="$1"
  if [[ "$raw" =~ ([A-Z][A-Z0-9]+-[0-9]+) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  printf 'Could not parse issue key from: %s\n' "$raw" >&2
  return 1
}

_load_kv_file() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    [[ "$line" == *"="* ]] || continue
    line="${line#$'\xef\xbb\xbf'}"
    [[ "$line" == export[[:space:]]* ]] && line="${line#export }"
    line="${line#"${line%%[![:space:]]*}"}"
    key="${line%%=*}"
    val="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    key="${key#"${key%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val#\"}"
    val="${val%\"}"
    val="${val#\'}"
    val="${val%\'}"
    case "$key" in
      JIRA_BASE | JIRA_BASE_URL | JIRA_TOKEN | JIRA_TOKEN_FILE | JIRA_ENV_FILE)
        if [[ -z "${!key:-}" ]]; then
          export "$key=$val"
        fi
        ;;
    esac
  done <"$file"
}

load_jira_env() {
  if [[ -n "${JIRA_ENV_FILE:-}" ]]; then
    _load_kv_file "$JIRA_ENV_FILE" || true
  fi
  _load_kv_file "${PWD}/.env" || true
  local walk="$PWD"
  local i
  for i in 1 2 3 4 5 6 7 8; do
    walk="$(cd "$walk/.." && pwd)" || break
    _load_kv_file "${walk}/.env" || true
  done
  walk="${_atlassian_scripts_dir}"
  for i in 1 2 3 4 5 6 7 8; do
    _load_kv_file "${walk}/.env" || true
    walk="$(cd "$walk/.." && pwd)" || break
  done

  if [[ -z "${JIRA_TOKEN:-}" ]]; then
    local token_file
    while IFS= read -r token_file; do
      [[ -f "$token_file" ]] || continue
      JIRA_TOKEN="$(tr -d '[:space:]' <"$token_file")"
      export JIRA_TOKEN
      break
    done < <(_jira_token_candidates)
  fi

  if [[ -z "${JIRA_BASE:-}" && -n "${JIRA_BASE_URL:-}" ]]; then
    JIRA_BASE="$JIRA_BASE_URL"
    export JIRA_BASE
  fi

  if [[ -z "${JIRA_BASE:-}" ]]; then
    printf 'Missing JIRA_BASE (or JIRA_BASE_URL). Set it in the environment or .env.\n' >&2
    return 1
  fi
  if [[ -z "${JIRA_TOKEN:-}" ]]; then
    printf 'Missing Jira token. Put a Data Center PAT in ~/.config/atlassian/jira.token or %%USERPROFILE%%/.config/atlassian/jira.token, or set JIRA_TOKEN / JIRA_ENV_FILE.\n' >&2
    return 1
  fi
  return 0
}

find_transition_id() {
  local want="$1"
  jira_python -c '
import json, sys
want = sys.argv[1].strip().lower()
data = json.load(sys.stdin)
for t in data.get("transitions") or []:
    names = [t.get("name") or "", (t.get("to") or {}).get("name") or ""]
    if any(n.lower() == want for n in names if n):
        print(t["id"])
        raise SystemExit(0)
sys.stderr.write("No transition matching %r\n" % sys.argv[1])
raise SystemExit(1)
' "$want"
}

_jira_dry_run() {
  local method="$1" url="$2" body="${3:-}"
  printf 'curl -sS -X %s \\\n' "$method"
  printf "  -H 'Authorization: Bearer ***' \\\\\n"
  printf "  -H 'Accept: application/json'"
  if [[ -n "$body" ]]; then
    printf " \\\\\n  -H 'Content-Type: application/json' \\\\\n"
    printf "  --data %s \\\\\n" "$body"
  else
    printf " \\\\\n"
  fi
  printf "  '%s'\n" "$url"
}

jira() {
  local dry=0
  if [[ "${1:-}" == "--dry-run" ]]; then
    dry=1
    shift
  fi
  local method="${1:-}"
  local path="${2:-}"
  shift 2 || true
  local body=""
  if [[ $# -gt 0 ]]; then
    body="$1"
  fi
  load_jira_env || return 1
  local url
  url="$(jira_url "$path")"
  if [[ "$dry" -eq 1 || "${JIRA_DRY_RUN:-}" == "1" ]]; then
    _jira_dry_run "$method" "$url" "$body"
    return 0
  fi
  local tmp http_code
  tmp="$(mktemp)"
  local curl_args=(-sS -o "$tmp" -w "%{http_code}" -X "$method"
    -H "Authorization: Bearer ${JIRA_TOKEN}"
    -H "Accept: application/json")
  if [[ -n "$body" ]]; then
    curl_args+=(-H "Content-Type: application/json" --data "$body")
  fi
  curl_args+=("$url")
  http_code="$(curl "${curl_args[@]}")" || {
    local curl_rc=$?
    rm -f "$tmp"
    printf 'curl failed talking to Jira (exit %s)\n' "$curl_rc" >&2
    return "$curl_rc"
  }
  if [[ "$http_code" != 2* ]]; then
    printf 'Jira HTTP %s for %s %s\n' "$http_code" "$method" "$url" >&2
    cat "$tmp" >&2
    printf '\n' >&2
    rm -f "$tmp"
    return 1
  fi
  cat "$tmp"
  printf '\n'
  rm -f "$tmp"
}

# Silence unused-dir warning when this file is sourced.
: "${_atlassian_scripts_dir}"
