# Agent-box terminal activity

The box supervisor starts the default `astrolift` tmux session. Other terminal
owners may run private tmux servers and register them for the same idle policy.
The container advertises `ASTROLIFT_AGENT_TMUX_REGISTRY`; the supervisor creates
that private directory before starting its default shell. Platform rendering
reserves this variable so model defaults, environment specs and secrets cannot
redirect it. Existing boxes need recreation to receive the new supervisor.

Before accepting or launching a transferred session, the terminal owner must:

1. Require the advertised registry and verify it is a private directory owned by
   the terminal user. Absence means this box does not support this contract.
2. Create a uniquely named `*.sock` symlink in the registry pointing to its
   absolute private tmux socket path. It may register before creating the socket.
   Reconnects reuse the same link; a conflicting registration must be rejected.
3. Retain the registration while its terminals may run. A stale link after the
   server exits is harmless. Use short paths within the Unix socket limit.

Every five seconds the supervisor reads the default session and all registered
servers. A box remains alive while at least one live pane exists. Closing the
default shell does not end a registered live terminal. Dead panes retained for
transcripts and missing sockets do not count as live work.

With a finite idle timeout, the box shuts down when there are no attached
clients on live sessions and no live pane's window has emitted output within the
timeout. Output from any registered window refreshes activity. An attached
read-only viewer also counts. A stopped/dead pane cannot refresh activity or
retain the box by keeping a viewer attached. A silently computing, detached
agent is still subject to the configured idle timeout; choose an appropriate
value or explicitly use `0` for no idle reaping. No synthetic heartbeat is used.

On idle shutdown the supervisor ends the default session and registered servers,
then exits PID 1. Exiting all live panes also ends PID 1, including with timeout
`0`. Box deletion remains a separate control-plane operation.
