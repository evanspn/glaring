#!/usr/bin/env bash
# End-to-end: a task crosses THREE pod boundaries as OpenRig queue rows
# (orchestrator -> builder -> qa -> orchestrator). Seats are terminal
# stand-ins; the logic is the same one a model-backed seat would run.
# Needs the test pods up:  ./glaring --pods tests/pods up --env-file tests/test.env
set -euo pipefail
pod() { local p=$1; shift; docker exec -e OPENRIG_SESSION_NAME="$p-worker@$p" "glaring-$p" "$@"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok   $*"; }
row_id() { jq -r '.qitemId' ; }

INPUT="glaring-$(date +%s)"
EXPECT=$(printf %s "$INPUT" | sha256sum | cut -d' ' -f1)

# 1. orchestrator -> builder
Q1=$(pod orchestrator rig queue create --host builder --destination builder-worker@builder \
  --summary "e2e: sha256 task" --body "sha256 of: $INPUT" | row_id)
[ -n "$Q1" ] && ok "orchestrator created $Q1 on the builder's daemon"

# 2. builder reads its own queue, does the work in ITS workspace volume
BODY=$(pod builder rig queue list --destination builder-worker@builder --full --json | jq -r --arg id "$Q1" '.[]|select(.qitemId==$id)|.body')
[ -n "$BODY" ] || fail "builder never received the row"
ok "builder received it (source: $(pod builder rig queue list --full --json | jq -r --arg id "$Q1" '.[]|select(.qitemId==$id)|.sourceSession'))"
pod builder rig queue claim "$Q1" >/dev/null
IN=${BODY#sha256 of: }
RESULT=$(printf %s "$IN" | sha256sum | cut -d' ' -f1)
pod builder sh -c "printf %s '$RESULT' > /workspace/result.txt"
ok "builder wrote its result into its own workspace volume"

# 3. builder -> qa (the result travels IN the row: no shared filesystem)
Q2=$(pod builder rig queue create --host qa --destination qa-worker@qa \
  --summary "e2e: verify sha256" --body "input=$IN result=$RESULT" | row_id)
ok "builder created $Q2 on QA's daemon"

# 4. qa verifies independently and answers the orchestrator
QBODY=$(pod qa rig queue list --destination qa-worker@qa --full --json | jq -r --arg id "$Q2" '.[]|select(.qitemId==$id)|.body')
QIN=${QBODY#input=}; QIN=${QIN%% result=*}; QRES=${QBODY##*result=}
[ "$(printf %s "$QIN" | sha256sum | cut -d' ' -f1)" = "$QRES" ] && VERDICT=PASS || VERDICT=FAIL
pod qa rig queue claim "$Q2" >/dev/null
Q3=$(pod qa rig queue create --host orchestrator --destination orchestrator-worker@orchestrator \
  --summary "e2e: verdict $VERDICT" --body "verdict=$VERDICT task=$Q1" | row_id)
ok "qa verified independently: $VERDICT"

# 5. orchestrator sees the verdict
GOT=$(pod orchestrator rig queue list --destination orchestrator-worker@orchestrator --full --json | jq -r --arg id "$Q3" '.[]|select(.qitemId==$id)|.body')
[ "$GOT" = "verdict=PASS task=$Q1" ] || fail "orchestrator did not get a PASS verdict (got: $GOT)"
[ "$RESULT" = "$EXPECT" ] || fail "result mismatch"
ok "orchestrator received verdict=PASS; result $EXPECT matches"

# the boundary held: QA cannot see the builder's workspace
if pod qa test -e /workspace/result.txt; then fail "qa can see the builder's result file"; fi
ok "qa has no access to the builder's workspace (rows were the only bridge)"
echo "E2E PASS"
