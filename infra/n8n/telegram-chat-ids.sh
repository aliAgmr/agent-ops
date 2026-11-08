#!/usr/bin/env bash
# Lists the people who opened a private chat with the team bot (needed for private messages:
# a bot can only DM someone who pressed Start / wrote to it first), and with --write fills
# people.yaml telegram_chat_id by matching telegram_username (case-insensitive).
#
#   bash infra/n8n/telegram-chat-ids.sh [--write]
#
# The bot token never leaves the n8n container: the credential "agent-ops-telegram" is exported decrypted
# to a temp file inside it (as user node), read and deleted right away, and only getUpdates' private
# chats are printed. Telegram keeps updates for ~24 h, so ask people to message the bot shortly
# before running this. After --write, rebuild + import the flow (build-flow.sh --import).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CONTAINER="${N8N_CONTAINER:-n8n-n8n-1}"
# The Telegram credential's id (looked up by name when not given).
CRED_ID="${TELEGRAM_CRED_ID:-$(docker exec "$CONTAINER" sh -c 'n8n export:credentials --all --output=/tmp/.creds.json >/dev/null 2>&1; cat /tmp/.creds.json 2>/dev/null; rm -f /tmp/.creds.json' \
  | jq -r '.[]? | select(.name == "agent-ops-telegram") | .id' | head -1)}"
[ -n "$CRED_ID" ] || { echo "no n8n credential named agent-ops-telegram (or set TELEGRAM_CRED_ID)" >&2; exit 1; }
PEOPLE="${PEOPLE_FILE:-$ROOT/people.yaml}"
[ -f "$PEOPLE" ] || { echo "no $PEOPLE: copy people.template.yaml to people.yaml first (SETUP.md does)" >&2; exit 1; }
WRITE=0; [ "${1:-}" = "--write" ] && WRITE=1

chats="$(docker exec -i -u node -e CRED_ID="$CRED_ID" "$CONTAINER" sh -c '
  umask 077; f=/tmp/.c.$$.json
  n8n export:credentials --id="$CRED_ID" --decrypted --output="$f" </dev/null >/dev/null 2>&1
  node - "$f"; rc=$?; rm -f "$f"; exit $rc' <<'JS'
const fs = require('fs');
const f = process.argv[2];
let token = '';
try { token = (JSON.parse(fs.readFileSync(f, 'utf8'))[0] || {}).data.accessToken || ''; } catch (e) {}
try { fs.unlinkSync(f); } catch (e) {}
if (!token) { console.error('credential not readable (wrong id?)'); process.exit(2); }
const clean = (s) => String(s).split(token).join('<token>');
(async () => {
  const r = await fetch(`https://api.telegram.org/bot${token}/getUpdates?allowed_updates=[]`);
  const j = await r.json();
  if (!j.ok) { console.error('getUpdates: ' + clean(j.description || r.status)); process.exit(3); }
  const chats = {};
  for (const u of j.result) {
    const m = u.message || u.edited_message || u.my_chat_member || (u.callback_query && u.callback_query.message) || {};
    const c = m.chat;
    if (!c || c.type !== 'private') continue;
    const last = Math.max((chats[c.id] || {}).last || 0, m.date || 0);
    chats[c.id] = { chat_id: c.id, username: c.username || '', first_name: c.first_name || '', last_name: c.last_name || '', last };
  }
  console.log(JSON.stringify(Object.values(chats)));
})().catch((e) => { console.error('error: ' + clean(e.message)); process.exit(4); });
JS
)"
# The Telegram poller (infra/telegram/poll.py) reads the bot's updates every minute, so a Start
# pressed earlier may be gone from getUpdates: add the private chats it recorded.
polled="$(python3 -I "$ROOT/infra/telegram/poll.py" private 2>/dev/null || echo '[]')"
chats="$(jq -cn --argjson a "${chats:-[]}" --argjson b "${polled:-[]}" \
  '$a + $b | group_by(.chat_id) | map(max_by(.last)) | map(.username //= "" | .first_name //= "" | .last_name //= "")')"

ruby -E UTF-8 -rjson -ryaml -e '
  chats = JSON.parse(ARGV[0]); file = ARGV[1]; write = ARGV[2] == "1"
  if chats.empty?
    puts "No private chats in the last ~24 h. Ask people to open the bot and press Start (or send it any message), then run this again."
  else
    puts "Private chats with the bot (last ~24 h):"
    chats.each do |c|
      t = Time.at(c["last"]).utc.strftime("%Y-%m-%d %H:%M UTC")
      name = [c["first_name"], c["last_name"]].reject(&:empty?).join(" ")
      printf("  %-12s @%-18s %-24s last message %s\n", c["chat_id"], c["username"].empty? ? "-" : c["username"], name, t)
    end
  end
  exit 0 unless write
  people = (YAML.load_file(file) || {})["people"] || {}
  by_user = chats.reject { |c| c["username"].empty? }.to_h { |c| [c["username"].downcase, c["chat_id"].to_s] }
  set = {}
  people.each do |key, v|
    v ||= {}
    u = v["telegram_username"].to_s.delete("@").downcase
    id = by_user[u] unless u.empty?
    set[key] = id if id && v["telegram_chat_id"].to_s != id
  end
  # Edit the file line by line so comments and layout stay as they are.
  cur = nil; in_people = false
  lines = File.readlines(file).map do |l|
    in_people = true if l =~ /^people:/
    in_people = false if l =~ /^\S/ && l !~ /^people:/
    cur = $1 if in_people && l =~ /^  ([A-Za-z0-9_-]+):\s*(#.*)?$/
    if in_people && cur && set[cur] && l =~ /^    telegram_chat_id:[^#\n]*(#.*)?$/
      note = $1
      head = "    telegram_chat_id: #{set[cur]}"
      note ? "#{head.ljust(28)} #{note}\n" : "#{head}\n"
    else l end
  end
  File.write(file, lines.join)
  if set.empty? then puts "people.yaml: nothing to change"
  else set.each { |k, id| puts "people.yaml: #{k}.telegram_chat_id = #{id}" } end
  mapped = people.values.map { |v| (v || {})["telegram_chat_id"].to_s }.reject(&:empty?) + set.values
  manual = people.select { |_, v| v = v || {}; v["telegram_username"].to_s.empty? && v["telegram_chat_id"].to_s.empty? }.keys
  unknown = chats.reject { |c| mapped.include?(c["chat_id"].to_s) }
  puts "No telegram_username, map by hand if they are above: #{manual.join(", ")}" unless manual.empty?
  unknown.each { |c| puts "Not matched to anyone: #{c["chat_id"]} @#{c["username"]} #{c["first_name"]} #{c["last_name"]}" }
  puts "Then rebuild and import the flow: build-flow.sh --import" unless set.empty?
' "$chats" "$PEOPLE" "$WRITE"
