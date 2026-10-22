#!/bin/zsh
# TASK-23 AC#5 regression harness.
#
# With the probe cache stamp deleted, starting N owlex-servers concurrently must
# produce at most N grok probes, 0 owlex-servers parented to a probe, 0 orphaned
# MCP processes, and 0 grok MCP-server boots.
#
# Usage: scripts/verify_grok_probe_isolation.sh [N]   (default N=5)
#
# MEASUREMENT NOTE (this bit is load-bearing): match candidate processes on
# `comm`, never with a bare `pgrep -f <prompt>`. This script's own text contains
# the probe prompt, so an argv-substring match counts the measuring shell itself
# and reports phantom probes. An earlier run of this check reported 10 probes and
# 10 "nested" owlex-servers purely from that self-match.
set -u
N=${1:-5}
OUT=$(mktemp -d)
MARK="$OUT/mark"
GROK_MCP_LOGS="${GROK_HOME:-$HOME/.grok}/logs/mcp"

probe_pids() { ps -eo pid,comm,args | awk '$2 ~ /grok/ && index($0,"Reply with exactly")>0 {print $1}'; }
owlex_pids() { ps -eo pid,comm,args | awk '$0 ~ /bin\/owlex-server/ && $2 ~ /python/ {print $1}'; }
seat_pids()  { ps -eo pid,comm,args | awk '$2 ~ /grok/ && index($0,"council deliberation")>0 {print $1}'; }

CACHE="${OWLEX_HOME:-$HOME/.owlex}/cache"
# BOTH stamps. Deleting only the success stamp left the short-TTL FAILURE stamp
# in place, so every server cache-hit, no probe ran, and the script printed PASS
# having measured nothing (reproduced: max_probes=0, exit 0).
rm -f "$CACHE/grok-probe.json" "$CACHE/grok-probe-fail.json"
touch "$MARK"; sleep 1

if [ ! -d "$GROK_MCP_LOGS" ]; then
  # `find` on a missing dir prints nothing, which is indistinguishable from a
  # real pass — refuse rather than silently turn AC#3 into a no-op.
  print "FAILED: grok MCP log dir not found: $GROK_MCP_LOGS (set GROK_HOME)"
  exit 2
fi

pre_seats=$(seat_pids | wc -l | tr -d ' ')
for i in $(seq 1 "$N"); do ( sleep 70 | owlex-server > "$OUT/srv.$i.out" 2> "$OUT/srv.$i.err" ) & done

max_probes=0; max_nested=0; max_orphans=0; max_seats=$pre_seats
for _ in $(seq 1 65); do
  p=$(probe_pids | wc -l | tr -d ' '); (( p > max_probes )) && max_probes=$p
  s=$(seat_pids  | wc -l | tr -d ' '); (( s > max_seats  )) && max_seats=$s
  n=0
  for op in $(owlex_pids); do
    pp=$(ps -o ppid= -p "$op" 2>/dev/null | tr -d ' ')
    [[ -n "$pp" ]] && probe_pids | grep -qx "$pp" && n=$((n+1))
  done
  (( n > max_nested )) && max_nested=$n
  o=$(ps -eo ppid,args | grep -E "[n]pm exec" | awk '$1==1' | wc -l | tr -d ' ')
  (( o > max_orphans )) && max_orphans=$o
  sleep 1
done
wait

logs_touched=$(find "$GROK_MCP_LOGS" -newer "$MARK" -type f 2>/dev/null | wc -l | tr -d ' ')
probe_lines=$(grep -h "grok seat health-check" "$OUT"/srv.*.err 2>/dev/null | wc -l | tr -d ' ')

print "servers started        : $N"
print "probe log lines        : $probe_lines"
print "max concurrent probes  : $max_probes   (AC#5: 1..$N — 0 means vacuous)"
print "owlex-server под probe : $max_nested   (AC#1: 0)"
print "orphaned npm exec      : $max_orphans  (AC#2: 0)"
print "grok MCP logs touched  : $logs_touched (AC#3: 0)"
print "council seats in window: $max_seats    (confounder — must be 0 for a clean AC#3)"

fail=0
# green != exercised: exit 0 must not be reachable without real probes.
(( max_probes  < 1 )) && { print "FAIL: no probe ran at all — result is vacuous"; fail=1 }
(( probe_lines < N )) && { print "FAIL: only $probe_lines/$N servers reported a health-check"; fail=1 }
(( max_probes  > N )) && { print "FAIL: more probes than servers"; fail=1 }
(( max_nested  > 0 )) && { print "FAIL: owlex-server spawned under a grok probe"; fail=1 }
(( max_orphans > 0 )) && { print "FAIL: orphaned MCP processes"; fail=1 }
(( logs_touched > 0 && max_seats == 0 )) && { print "FAIL: probe booted grok MCP servers"; fail=1 }
(( logs_touched > 0 && max_seats > 0 )) && print "WARN: MCP logs touched, but $max_seats council seat(s) ran concurrently — rerun with no council active"
rm -rf "$OUT"
(( fail == 0 )) && print "PASS" || print "FAILED"
exit $fail
