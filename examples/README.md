# Examples

Small, self-contained programs you can run under CmdScope to see what a session looks
like.

## `demo_session.py`

Creates, modifies, and deletes files, spawns a child process, and opens a loopback
listener.

```console
$ python examples/demo_session.py   # in a scratch directory
$ cmdscope run -- python ../cmdscope/examples/demo_session.py
```

Copy the script into a scratch directory first: it writes into the current working
directory.

## `listener_session.py`

Opens a loopback TCP listener for a few seconds so the network collector has something to
observe.

```console
$ cmdscope run -- python ../cmdscope/examples/listener_session.py
```

## Tips

- Point CmdScope at a temporary directory so the report only contains what the example
  changed.
- Use `cmdscope report <id> --format html --output report.html` to inspect the session in
  a browser.
- Use `cmdscope compare <id1> <id2>` to diff two runs.
