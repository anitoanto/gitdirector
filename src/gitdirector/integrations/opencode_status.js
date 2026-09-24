// GitDirector status reporter for OpenCode.
//
// Loaded by OpenCode as a plugin (GitDirector passes its path through
// OPENCODE_CONFIG_CONTENT when it launches OpenCode inside a tmux session).
// It listens to OpenCode's event bus and stamps the session's status on the
// tmux pane it runs in, using the same option Claude Code's hooks use:
//
//   @gitdirector_agent_state = running | waiting | idle
//
// OpenCode can hold several sessions in one process, so the report is the
// most urgent state across all of them: waiting if any session is blocked
// on the user, running if any is busy, idle otherwise. OpenCode reports an
// interrupted turn as idle itself, so no extra safeguard is needed.

export const GitDirectorStatus = async ({ $ }) => {
  const pane = process.env.TMUX_PANE;
  if (!pane) {
    return {};
  }

  const OPTION = "@gitdirector_agent_state";
  const busy = new Set(); // session IDs with a turn in progress
  const waiting = new Map(); // request ID -> session ID blocked on the user
  let reported = null;
  // Events can overlap; running the writes one at a time, and deciding the
  // state only when a write starts, keeps the last write the current state.
  let chain = Promise.resolve();

  const report = () => {
    chain = chain.then(async () => {
      const state = waiting.size > 0 ? "waiting" : busy.size > 0 ? "running" : "idle";
      if (state === reported) {
        return;
      }
      try {
        const result = await $`tmux set-option -p -t ${pane} ${OPTION} ${state}`.quiet().nothrow();
        if (result.exitCode === 0) {
          reported = state;
        }
      } catch {
        // Reporting must never disturb the agent.
      }
    });
    return chain;
  };

  const forget = (sessionID) => {
    busy.delete(sessionID);
    for (const [requestID, owner] of waiting) {
      if (owner === sessionID) {
        waiting.delete(requestID);
      }
    }
  };

  await report();

  return {
    event: async ({ event }) => {
      const props = event.properties ?? {};
      switch (event.type) {
        case "session.status":
          if (props.status?.type === "busy") {
            busy.add(props.sessionID);
          } else {
            forget(props.sessionID);
          }
          break;
        case "session.idle":
        case "session.error":
          forget(props.sessionID);
          break;
        case "session.deleted":
          forget(props.sessionID ?? props.info?.id);
          break;
        case "permission.asked":
        case "question.asked":
          waiting.set(props.id, props.sessionID);
          break;
        case "permission.replied":
        case "question.replied":
        case "question.rejected":
          waiting.delete(props.requestID ?? props.permissionID ?? props.id);
          break;
        default:
          return;
      }
      await report();
    },
  };
};
