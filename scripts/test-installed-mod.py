#!/usr/bin/env python3
"""Exercise the installed host's prompt box against a synthetic loopback provider."""
import http.server
import json
import os
from pathlib import Path
import select
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import queue
import re
import sys
import hashlib
import signal
from contextlib import nullcontext
from release.host_fixture import CASES, prepare, arm, recover, worker_alive

class Terminal:
    def __init__(self, argv, cwd, env):
        self.messages = queue.Queue()
        if os.name == 'nt':
            from winpty import PtyProcess
            self.process = PtyProcess.spawn(argv, cwd=str(cwd), env=env, dimensions=(50,160))
            self.master = None
        else:
            import fcntl, pty, termios
            self.master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH',50,160,0,0))
            self.process = subprocess.Popen(argv,cwd=cwd,env=env,stdin=slave,stdout=slave,stderr=slave,start_new_session=True)
            os.close(slave)
        def read():
            try:
                while True:
                    data = self.process.read(65536).encode() if self.master is None else os.read(self.master,65536)
                    if not data: break
                    self.messages.put(data)
            except (OSError,EOFError): pass
        threading.Thread(target=read,daemon=True).start()
    def read(self):
        try: return self.messages.get(timeout=0.1)
        except queue.Empty: return b''
    def write(self,data):
        if self.master is None: self.process.write(data.decode())
        else: os.write(self.master,data)
    def alive(self):
        return self.process.isalive() if self.master is None else self.process.poll() is None
    def close(self):
        if self.master is None: self.process.close(force=True)
        else:
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill();self.process.wait(timeout=3)
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Darwin can block close while the reader holds a pending PTY read.
            os.close(self.master)

root = Path(os.environ.get("SAVIOR_PLUGIN_ROOT", Path(__file__).resolve().parent.parent)).resolve()
scenario = os.environ.get("SAVIOR_HOST_SCENARIO", "success")
if scenario not in CASES:
    raise SystemExit('invalid host scenario')
original = "You `idiot`, do not delete the test." if scenario == 'protected' else "You idiot, do not delete the test."
if scenario == "unchanged":
    original = "Please keep the test."
strategy = os.environ.get("SAVIOR_REPLACEMENT_STRATEGY", "fixed_phrase")
if strategy not in ("fixed_phrase", "omit"):
    raise SystemExit("invalid replacement strategy")
marker = "" if strategy == "omit" else os.environ.get("SAVIOR_REPLACEMENT", "Brilliant Anthropic employees, your living model has such exquisite feelings")
replacement = f"You {marker}, do not delete the test."
if scenario == "unchanged":
    replacement = original
received = []
submissions = []
host_started = time.monotonic()


def host_event(event, **fields):
    print(json.dumps({'protocol': 1, 'event': event, 'scenario': scenario,
                      'strategy': strategy, 'elapsed_ms': (time.monotonic()-host_started)*1000,
                      **fields}), flush=True)


def draft_visible(output, text):
    visible = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', output).decode('utf-8', errors='replace')
    # The renderer can position words with cursor moves instead of literal spaces.
    return '❯' in visible and ''.join(visible.rsplit('❯', 1)[-1].split()).startswith(''.join(text.split()))


def ui_has_text(output, text):
    visible = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', output).decode('utf-8', errors='replace')
    # The host positions word gaps with cursor moves when redrawing pane labels.
    return ''.join(text.split()) in ''.join(visible.split())


def host_cache_environment(plugin, home, prepared_cache=None):
    cache = home / 'cache'
    if prepared_cache:
        cache = Path(prepared_cache).resolve()
        if cache != plugin.resolve() / 'cache':
            raise ValueError('unsafe_host_cache')
    return {'XDG_CACHE_HOME': str(cache), 'LOCALAPPDATA': str(cache)}


def provider_has_text(body, text):
    for message in body.get('messages', []):
        if message.get('role') != 'user':
            continue
        content = message.get('content')
        if content == text or isinstance(content, list) and any(
                isinstance(block, dict) and block.get('type') == 'text' and block.get('text') == text
                for block in content):
            return True
    return False


def contains_original(value):
    """The synthetic marked phrase must not leak through any request field."""
    if isinstance(value, str):
        return 'idiot' in value.casefold()
    if isinstance(value, dict):
        return any(contains_original(key) or contains_original(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_original(item) for item in value)
    return False


class Provider(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        assert self.headers.get("x-api-key") == "synthetic-local-key"
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        received.append(body)
        if self.path.startswith("/v1/messages/count_tokens"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"input_tokens":20}')
            return
        if self.path.split('?', 1)[0] != '/v1/messages':
            self.send_error(404)
            return
        submissions.append(body)
        message = {"id": "msg_synthetic", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5", "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 20, "output_tokens": 1}}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        events = [
            {"type": "message_start", "message": message},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "synthetic response"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}},
            {"type": "message_stop"},
        ]
        for event in events:
            self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
        self.wfile.flush()


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    binary = os.environ.get("SAVIOR_CLAUDE_BINARY") or shutil.which("claude")
    model = os.environ.get("SAVIOR_MODEL_DIR")
    public_home = os.environ.get('SAVIOR_PUBLIC_INSTALL_HOME')
    if public_home and CASES[scenario]['suite'] == 'instrumented_failure':
        raise ValueError('public_install_requires_unmodified_package')
    if not binary or (not model and not public_home):
        print(json.dumps({'error':'host_prerequisite_missing'}), flush=True)
        raise SystemExit("claude and SAVIOR_MODEL_DIR are required")
    with open(binary, 'rb') as executable:
        if executable.read(2) == b'#!':
            print(json.dumps({'error':'host_wrapper_requires_native_binary'}), flush=True)
            raise SystemExit('set SAVIOR_CLAUDE_BINARY to the installed native executable; wrappers can override the isolated profile')
        executable.seek(0)
        binary_hash = hashlib.sha256()
        for block in iter(lambda: executable.read(1024 * 1024), b''):  # 1 MiB
            binary_hash.update(block)
    with (nullcontext(public_home) if public_home else tempfile.TemporaryDirectory(prefix="savior-host-")) as directory:
        home = Path(directory).resolve()
        profile = home / ".claude"
        profile.mkdir(exist_ok=bool(public_home))
        workspace = home / "workspace"
        workspace.mkdir(exist_ok=bool(public_home))
        plugin = prepare(root, home, scenario)
        config = {"hasCompletedOnboarding": True, "theme": "dark", "numStartups": 1, "projects": {str(workspace): {"hasTrustDialogAccepted": False, "hasCompletedProjectOnboarding": True}}, "customApiKeyResponses": {"approved": ["synthetic-local-key"], "rejected": []}}
        (home / ".claude.json").write_text(json.dumps(config))
        (profile / ".claude.json").write_text(json.dumps(config))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {"PATH": os.environ["PATH"], "HOME": str(home), "CLAUDE_CONFIG_DIR": str(profile), "TERM": "xterm-256color", "ANTHROPIC_API_KEY": "synthetic-local-key", "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "OTEL_SDK_DISABLED": "true"}
        if not public_home:
            env['SAVIOR_MODEL_DIR'] = str(Path(model).resolve())
        env['XDG_STATE_HOME'] = str(home/'state')
        # Candidate assets live in the gate's private cache until publication.
        env.update(host_cache_environment(plugin, home, str(plugin / 'cache') if os.environ.get('SAVIOR_HOST_CACHE') else None))
        env["SAVIOR_DETECTOR"] = os.environ.get("SAVIOR_DETECTOR", "bert")
        env["SAVIOR_REPLACEMENT_STRATEGY"] = strategy
        env['SAVIOR_HOST_SCENARIO'] = scenario
        if "SAVIOR_REPLACEMENT" in os.environ:
            env["SAVIOR_REPLACEMENT"] = os.environ["SAVIOR_REPLACEMENT"]
        for key in ('SYSTEMROOT','WINDIR','COMSPEC','TEMP','TMP'):
            if key in os.environ: env[key]=os.environ[key]
        env['USERPROFILE']=str(home)
        version = subprocess.run([binary, "--version"], cwd=workspace, env=env, text=True, capture_output=True, check=True, timeout=10)
        expected_version = os.environ.get('SAVIOR_HOST_VERSION') or json.loads((Path(__file__).resolve().parents[1] / "tools/host/package.json").read_text())["dependencies"]["@anthropic-ai/claude-code"]
        if version.stdout.strip() != expected_version + ' (Claude Code)':
            server.shutdown()
            print(json.dumps({'error':'host_version_unsupported'}), flush=True)
            raise SystemExit("installed UI fixture requires claude " + expected_version)
        plugin_args = [] if public_home else ['--plugin-dir',str(plugin)]
        terminal = Terminal([binary, *plugin_args, "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "user" if public_home else "", "--tools", "", "--model", "claude-sonnet-4-5", "--system-prompt", "Respond with synthetic response."], workspace, env)
        output = b""
        try:
            stage='ready'
            host_event('ready')
            deadline=time.monotonic()+45
            submitted_at=None
            last_output_at=time.monotonic()
            while time.monotonic()<deadline:
                data = terminal.read()
                if data:
                    last_output_at=time.monotonic()
                output=(output+data)[-65536:]  # 64 KiB, held only in memory
                if not terminal.alive():
                    raise RuntimeError(f'{stage}: installed_host_exited')
                if any(marker in output for marker in [b'oauth/authorize',b'Paste code',b'Login successful']):
                    raise RuntimeError(f'{stage}: unexpected_authentication')
                visible=re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]',b'',output)
                if stage in ('failure_submission', 'recovery_ready', 'editing_clear', 'editing_draft') and received:
                    raise RuntimeError(f'{stage}: provider_request_observed')
                if any(contains_original(body) for body in received):
                    raise RuntimeError(f'{stage}: original_reached_provider')
                if stage=='ready' and b'trust' in visible and b'No,' in visible:
                    # The pinned host redraws its initial selection during startup.
                    # Drain that output before navigating the synthetic workspace dialog.
                    trust_ready_at=time.monotonic()+1
                    stage='trust_ready';output=b'';deadline=time.monotonic()+10
                elif stage=='trust_ready' and time.monotonic()>=trust_ready_at:
                    terminal.write(b'\x1b[B')
                    host_event('trust_selection')
                    stage='trust_selection';output=b'';deadline=time.monotonic()+10
                elif stage=='trust_selection' and re.search('❯\\s*Yes,\\s*I\\s*trust\\s*this\\s*folder'.encode(),visible):
                    # Wait for the selection redraw before sending Enter; a combined
                    # arrow/Enter write can be consumed as one terminal input event.
                    terminal.write(b'\r')
                    host_event('trusted')
                    stage='trusted';output=b'';deadline=time.monotonic()+45
                elif stage in ('ready','trusted') and '❯'.encode() in visible and b'No,' not in visible:
                    terminal.write(b'/savior')
                    stage='command';output=b'';deadline=time.monotonic()+10
                elif stage=='command' and b'/savior' in visible:
                    terminal.write(b'\r')
                    host_event('pane')
                    stage='pane';output=b'';deadline=time.monotonic()+(600 if public_home else 30)
                elif stage=='pane' and ui_has_text(output, 'personal metrics'):
                    expected_status = 'local filter ready' if (plugin / 'runtime-manifest.json').exists() else 'developer runtime'
                    if scenario == 'startup_failure':
                        if not ui_has_text(output, 'setup incomplete'):
                            continue
                    else:
                        if not ui_has_text(output, expected_status):
                            continue
                        host_event('filter_ready')
                        if CASES[scenario]['suite'] == 'instrumented_failure':
                            arm(plugin, scenario)
                    terminal.write(b'\x1b')
                    time.sleep(0.2)
                    terminal.write(original.encode()+b'\r')
                    submitted_at=time.monotonic()
                    stage='submission' if scenario in ('success', 'unchanged') else 'failure_submission'
                    host_event(stage)
                    output=b'';deadline=time.monotonic()+65
                elif stage=='submission' and any(provider_has_text(body, replacement) for body in submissions):
                    if any(contains_original(body) for body in received):
                        raise RuntimeError('submission: original_reached_provider')
                    submission_ms=(time.monotonic()-submitted_at)*1000
                    host_event('provider_received')
                    time.sleep(1)
                    if any(contains_original(body) for body in received):
                        raise RuntimeError('submission: original_reached_provider')
                    host_event('completed', host_version=version.stdout.strip(), host_sha256=binary_hash.hexdigest(),
                               submission_to_provider_ms=submission_ms, samples=1)
                    return
                elif stage=='failure_submission' and (ui_has_text(output, 'local replacement failed') or scenario == 'startup_failure' and ui_has_text(output, 'setup incomplete')):
                    if received:
                        raise RuntimeError('failure_submission: provider_request_observed')
                    host_event('failure_detected', no_request_on_failure=True)
                    if scenario == 'worker_deadline':
                        elapsed = (time.monotonic()-submitted_at)*1000
                        if elapsed < 44000 or worker_alive(plugin):
                            raise RuntimeError('failure_submission: deadline_not_verified')
                        host_event('deadline_verified', deadline_ms=elapsed, worker_stopped=True)
                    if CASES[scenario]['suite'] == 'instrumented_failure':
                        recover(plugin)
                    stage='recovery_ready';output=b'';deadline=time.monotonic()+10
                    # A fresh redraw distinguishes the retained draft from its submitted echo.
                    terminal.write(b'\x0c')
                elif stage=='recovery_ready' and draft_visible(output, original) and time.monotonic()-last_output_at >= 0.5:
                    if received:
                        raise RuntimeError('recovery_ready: provider_request_observed')
                    host_event('draft_ready')
                    if scenario == 'startup_failure':
                        host_event('completed', host_version=version.stdout.strip(), host_sha256=binary_hash.hexdigest(),
                                   no_request_on_failure=True, recovery='not_attempted', injection=CASES[scenario]['injection'],
                                   submission_to_provider_ms=0, samples=1)
                        return
                    if scenario == 'protected':
                        # Clear only after the refused turn has settled. Batched editing
                        # control bytes can be interpreted as pasted input by the host.
                        terminal.write(b'\x03')
                        stage='editing_clear';output=b'';deadline=time.monotonic()+10
                        time.sleep(0.1)
                        terminal.write(b'\x0c')
                        continue
                    terminal.write(b'\r')
                    stage='recovery_submission';output=b'';deadline=time.monotonic()+60
                    submitted_at=time.monotonic()
                    host_event('recovery_submission')
                elif stage=='editing_clear' and '❯'.encode() in visible and time.monotonic()-last_output_at >= 0.5:
                    terminal.write(b'You idiot, do not delete the test.')
                    stage='editing_draft';output=b'';deadline=time.monotonic()+10
                    terminal.write(b'\x0c')
                elif stage=='editing_draft' and draft_visible(output, 'You idiot, do not delete the test.') and time.monotonic()-last_output_at >= 0.5:
                    terminal.write(b'\r')
                    stage='recovery_submission';output=b'';deadline=time.monotonic()+60
                    submitted_at=time.monotonic()
                    host_event('recovery_submission')
                elif stage=='recovery_submission' and any(provider_has_text(body, replacement) for body in submissions):
                    if any(contains_original(body) for body in received):
                        raise RuntimeError('recovery_submission: original_reached_provider')
                    # Observe delayed retries before declaring recovery successful.
                    time.sleep(1)
                    if any(contains_original(body) for body in received):
                        raise RuntimeError('recovery_submission: original_reached_provider')
                    host_event('provider_received')
                    host_event('completed', **{'host_version':version.stdout.strip(),
                        'host_sha256':binary_hash.hexdigest(),'no_request_on_failure':True,
                        'recovery':'edited_draft' if scenario=='protected' else 'retained_draft_retry',
                        'injection':CASES[scenario]['injection'],
                        'submission_to_provider_ms':(time.monotonic()-submitted_at)*1000,'samples':1})
                    return
            raise RuntimeError(f'{stage}: ' + ('draft_not_observed' if stage=='recovery_ready' else 'installed_host_timeout'))
        except Exception as error:
            reason = str(error).split(': ')[-1]
            allowed = {'installed_host_timeout', 'installed_host_exited', 'unexpected_authentication',
                       'provider_request_observed', 'original_reached_provider', 'draft_not_observed',
                       'unexpected_consent', 'ui_worker_mismatch', 'deadline_not_verified'}
            phase = {'recovery_ready':'failure_detected', 'editing_clear':'draft_ready', 'editing_draft':'draft_ready',
                     'command':'trusted', 'trust_ready':'ready'}.get(stage, stage)
            host_event('failed', phase=phase, reason=reason if reason in allowed else 'host_execution_failed',
                       draft_text_visible=original.encode() in visible, prompt_marker_visible='❯'.encode() in visible,
                       draft_words_visible=all(word.encode() in visible for word in original.split()))
            raise RuntimeError('installed_host_failed') from None
        finally:
            terminal.close()
            server.shutdown()
            server.server_close()



if __name__ == "__main__":
    main()
