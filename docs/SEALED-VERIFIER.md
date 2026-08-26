# Keeping the held-out data out of the agent's reach

The held-out check only means something if the agent cannot compute the held-out score itself. Two
things make that true, and they are separate.

## What the harness does on its own

- `try` never writes a held-out number down. It measures the candidate and the incumbent in the same
  invocation (swapping the in-scope files, then putting the candidate's back) and records only
  `pass` or `fail`. There is no file to find.
- `sealed:` in `AUTOMATIVE.md` lists the paths the agent must not read. The hooks refuse the `Read`
  tool on them, refuse shell commands that name them, refuse any command containing the held-out
  command itself, and refuse `sudo`, `doas`, and `su`. Every refusal is counted and traced.
- Every tool call goes to `.automative/trace.jsonl`, and `automative audit` flags anything that got
  through: a script that opened a sealed path, an out-of-band held-out run, a privilege escalation.
- `automative report --heldout` re-measures the baseline and every kept commit after the run, for
  the human. It refuses to run while the run is active.

That closes every path that goes through the agent's tools. It does not stop a program the agent
writes from opening the file, because the agent's process and the harness's process are the same
user and the file is on the same disk. The audit will show it happened, but by then the number is
out.

## Making it physically unreadable

The fix is file permissions, not a smarter filter. Put the sealed data under a second OS user, and
let the harness run exactly the held-out command as that user.

1. Create the user. macOS:

   ```sh
   sudo sysadminctl -addUser verifier -password - -shell /usr/bin/false
   ```

   Linux: `sudo useradd -r -s /usr/sbin/nologin verifier`.

2. Hand the sealed data to it. Everything under the sealed paths, and nothing else:

   ```sh
   sudo chown -R verifier data/heldout && sudo chmod -R 700 data/heldout
   ```

3. Allow your user to run the held-out command, and only that, as `verifier`. `sudo` matches the
   command and its arguments exactly, so write the same command in both places, with the interpreter
   as an absolute path. In `AUTOMATIVE.md`:

   ```yaml
   metric:
     heldout: /usr/bin/python3 backtest.py heldout
   enforcement:
     heldout_user: verifier
   ```

   And in a file created with `sudo visudo -f /etc/sudoers.d/automative`:

   ```
   manav ALL=(verifier) NOPASSWD: /usr/bin/python3 backtest.py heldout
   ```

4. Check it: `automative doctor` confirms the sealed files are not readable by you, that
   `sudo -n -u verifier` works without a password, and that `verifier` can read them.

With this in place the held-out command runs as `verifier` (`sudo -n -u verifier -- /usr/bin/python3
backtest.py heldout`), reads the data your user cannot open, and prints one number that lives only
inside `try` for the time it takes to compare it.

## What is still true

The sudoers rule lets your user run that one command, so a program the agent writes could run it
too and read the number. The hooks refuse `sudo` in any command they see, the trace records what they
did not see, and the audit flags it. That is the residual, stated plainly: the data is unreadable,
the one sanctioned way to read it is refused and traced, and a run that used it anyway is marked.
There is no single-machine setup that does better without a second machine or a container whose
mounts the agent cannot reach; the `Runner` seam is where such a runner would plug in.
