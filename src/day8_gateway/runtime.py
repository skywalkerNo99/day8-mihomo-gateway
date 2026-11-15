"""Native supervisor. Decrypted settings and controller secrets stay in RAM."""
import asyncio
import contextlib
import fcntl
import http.client
import json
import os
from pathlib import Path
import signal
import socket

from . import catalog, adapter, config, controller, domain_policy, lan_proxy, monitor as metrics, node_updates, residual, verify


def control(root, command, **kwargs):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        timeouts = {'save-residual': 180, 'change-domain-rule': 60, 'verify-system-tun': 90, 'route-delays': 1500, 'verify': 160, 'apply-node': 160, 'health': 160}
        sock.settimeout(60 if command == 'configure-lan-proxy' else timeouts.get(command, 5))
        sock.connect(str(Path(root) / '.local/control.sock'))
        sock.sendall(json.dumps({'command': command, **kwargs}).encode() + b'\n')
        chunks = b''
        while b'\n' not in chunks:
            data = sock.recv(65536)
            if not data:
                raise RuntimeError('Native supervisor disconnected.')
            chunks += data
        return json.loads(chunks.split(b'\n')[0])


async def serve(payload, cfg, args, ready_fd=None):
    if cfg is None:
        raise ValueError('A complete native routing config is required.')
    root = Path(args.root)
    local = root / '.local'
    local.mkdir(mode=0o700, exist_ok=True)
    os.chmod(local, 0o700)
    lock = (local / 'run.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('A native supervisor is already running in this checkout.') from None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    ready = asyncio.Event()
    def make_node(node):
        dial = getattr(args, 'uplink', None)
        return adapter.Muon(node, dial=dial) if dial else adapter.Muon(node)

    policy = journal = None
    async def rendered(rows):
        interface = await asyncio.to_thread(catalog.uplink_name)
        return policy.render(rows, interface)

    if getattr(args, 'uplink', None) and cfg:
        policy = domain_policy.Policy(cfg, local / 'domain-rules.enc', args.update_key)
        cfg.update(await rendered(policy.rows))
        journal = metrics.DomainJournal()

    current_node = make_node(payload['day8'])
    task = asyncio.create_task(adapter.serve(payload['day8'], stop, port=args.adapter_port, ready=ready,
                                              current=lambda: current_node))
    update_lock = asyncio.Lock()
    child = None
    listener = None
    monitor = uplink_monitor = None
    socket_path = local / 'control.sock'
    restarts = 0
    hidden_upstreams = {payload['day8']['server'], payload['residual']['server']}

    async def log_connections(stream):
        while line := await stream.readline():
            hidden_upstreams.update((payload['day8']['server'], payload['residual']['server']))
            hidden = hidden_upstreams
            text = line.decode(errors='replace')
            if journal is not None:
                journal.append(text, hidden)
            if getattr(args, 'connection_logs', False):
                entry = metrics.timestamped_connection_line(text, hidden)
                if entry:
                    print(entry, flush=True)

    log_tasks = set()

    async def spawn():
        nonlocal child
        directory = local / 'mihomo'
        directory.mkdir(mode=0o700, exist_ok=True)
        show_logs = journal is not None or getattr(args, 'connection_logs', False)
        if show_logs:
            cfg['log-level'] = 'info'
        child = await asyncio.create_subprocess_exec(
            str(args.mihomo), '-d', str(directory), '-f', '/dev/stdin',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE if show_logs else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        if show_logs:
            log_task = asyncio.create_task(log_connections(child.stdout))
            log_tasks.add(log_task)
            log_task.add_done_callback(log_tasks.discard)
        child.stdin.write(json.dumps(cfg, ensure_ascii=False).encode())
        await child.stdin.drain()
        child.stdin.close()
        await child.stdin.wait_closed()
        for _ in range(100):
            if child.returncode is not None:
                raise RuntimeError('Native Mihomo rejected startup; check ports and configuration.')
            try:
                await asyncio.to_thread(controller.request, f'http://127.0.0.1:{args.controller_port}', cfg['secret'], '/version')
                # The control API can start before mixed-port accepts clients.
                # Require an actual SOCKS negotiation, not just controller HTTP.
                reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', args.port), 1)
                try:
                    writer.write(b'\x05\x01\x00')
                    await writer.drain()
                    if await asyncio.wait_for(reader.readexactly(2), 1) != b'\x05\x00':
                        raise OSError('Mixed proxy is not ready.')
                finally:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
                return
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                await asyncio.sleep(.1)
        raise RuntimeError('Native Mihomo did not become ready.')

    async def watch():
        nonlocal restarts
        while not stop.is_set():
            await child.wait()
            if stop.is_set():
                break
            restarts += 1
            await asyncio.sleep(1)
            try:
                await spawn()
            except (OSError, RuntimeError):
                stop.set()
                raise RuntimeError('Mihomo restart failed; unlock and start again.') from None

    def endpoint():
        return f'http://127.0.0.1:{args.controller_port}', cfg['secret']

    async def watch_direct_interface():
        while not stop.is_set():
            await asyncio.sleep(5)
            async with update_lock:
                parts = await rendered(policy.rows)
                if parts['proxies'] != cfg['proxies']:
                    await asyncio.to_thread(controller.apply, *endpoint(), {**cfg, **parts})
                    cfg.update(parts)

    async def apply_node(packet):
        nonlocal current_node
        key = args.update_key
        candidate = node_updates.unseal(args.vault, key, payload, packet)
        async with update_lock:
            url, secret = endpoint()
            live = await asyncio.to_thread(controller.request, url, secret, '/configs')
            rules = await asyncio.to_thread(controller.request, url, secret, '/rules')
            if [r.get('proxy') for r in rules['rules'][-2:]] != ['Day8-native', 'REJECT']:
                raise RuntimeError('Gateway policy is not active.')
            old = payload['day8']
            previous = current_node
            proxy = f"http://127.0.0.1:{live['mixed-port']}"
            updated, staged = config.node_update(cfg, payload, candidate)
            hidden_upstreams.add(candidate['server'])
            try:
                if staged is not cfg:
                    await asyncio.to_thread(controller.apply, url, secret, staged)
                current_node = make_node(candidate)
                payload['day8'] = candidate
                test = await asyncio.to_thread(verify.check, proxy, payload['expected_exit_ip'], 1)
                if not test['ok']:
                    raise ValueError('Exit verification failed.')
                if updated is not cfg:
                    await asyncio.to_thread(controller.apply, url, secret, updated)
                node_updates.save(local / 'node.enc', packet)
                cfg.update(updated)
            except BaseException:
                current_node = previous
                payload['day8'] = old
                if staged is not cfg:
                    try:
                        await asyncio.to_thread(controller.apply, url, secret, cfg)
                    except Exception:
                        stop.set()
                raise
        return {'ok': True}

    async def save_residual(edit):
        async with update_lock:
            try:
                new = residual.candidate(payload, args.update_key, edit)
            except ValueError as exc:
                return {'ok': False, 'error': str(exc)}
            try:
                verified = await asyncio.to_thread(residual.test_exit, new, args.adapter_port)
            except (OSError, ValueError, http.client.HTTPException):
                verified = False
            if not verified:
                return {'ok': False, 'error': '候选代理未通过 ChatGPT 出口核验，请检查接入地址、凭据和预期出口。'}
            if residual.settings(new) == residual.settings(payload):
                return {'ok': True}

            nodes = config.nodes(new, args.adapter_port)
            extra = [p for p in cfg['proxies'] if p['name'] not in (config.TRANSPORT, config.EGRESS)]
            updated = {**cfg, 'proxies': nodes + extra}
            try:
                await asyncio.to_thread(controller.apply, *endpoint(), updated)
                result = await asyncio.to_thread(verify.check, f'http://127.0.0.1:{args.port}', new['expected_exit_ip'], 1)
                if not result['ok']:
                    raise RuntimeError('Active route verification failed.')
                residual.save(root, args.vault, args.update_key, new)
            except Exception:
                try:
                    await asyncio.to_thread(controller.apply, *endpoint(), cfg)
                except Exception:
                    stop.set()  # Reload the last authenticated settings from disk.
                return {'ok': False, 'error': '应用或保存失败，后台服务使用之前的配置。'}
            policy.proxies = nodes
            cfg.update(updated)
            payload.update(residual.settings(new))
            return {'ok': True}

    async def connection(reader, writer):
        try:
            message = json.loads(await asyncio.wait_for(reader.readline(), 5))
            command = message.get('command')
            if command == 'status':
                result = {'ok': True, 'pid': os.getpid(), 'mode': 'standalone',
                          'adapter_port': args.adapter_port, 'mihomo_pid': child.pid if child else None,
                          'mihomo_restarts': restarts, 'proxy_port': args.port, 'node_update_version': 2}
                if hasattr(args, 'update_key'):
                    result['route_token'] = catalog.token(args.update_key, current_node.config)
            elif command == 'lan-proxy' and policy is not None:
                result = lan_proxy.status(cfg)
            elif command == 'configure-lan-proxy' and policy is not None:
                value = message['network']
                parts = lan_proxy.fields(value)
                async with update_lock:
                    try:
                        await asyncio.to_thread(controller.apply, *endpoint(), {**cfg, **parts})
                        lan_proxy.save(local / 'lan-proxy.json', value)
                    except Exception:
                        try:
                            await asyncio.to_thread(controller.apply, *endpoint(), cfg)
                        except Exception:
                            stop.set()
                        raise
                    cfg.update(parts)
                result = lan_proxy.status(cfg)
            elif command == 'route-delays' and hasattr(args, 'probes'):
                result = await args.probes.measure('manual')
            elif command == 'probe-status' and hasattr(args, 'probes'):
                result = args.probes.status()
            elif command == 'probe-interval' and hasattr(args, 'probes'):
                result = args.probes.configure(message['minutes'])
            elif command == 'verify-system-tun' and hasattr(args, 'uplink'):
                from . import tun_verify
                result = await asyncio.to_thread(tun_verify.check, *endpoint(), payload['expected_exit_ip'],
                                                 policy.ordinary_probe())
            elif command in ('get-residual', 'save-residual'):
                if policy is None:
                    result = {'ok': False, 'error': '请在 Mac 单 App 模式中编辑 residual。'}
                elif command == 'get-residual':
                    result = residual.form(payload, args.update_key)
                else:
                    result = await save_residual(message['edit'])
            elif command == 'domain-rules' and policy is not None:
                result = policy.snapshot()
            elif command == 'domain-events' and journal is not None:
                result = journal.search(message.get('query', ''))
            elif command == 'change-domain-rule' and policy is not None:
                async with update_lock:
                    try:
                        rows = policy.change(message)
                    except ValueError as exc:
                        result = {'ok': False, 'error': str(exc)}
                    else:
                        parts = await rendered(rows)
                        updated = {**cfg, **parts}
                        try:
                            await asyncio.to_thread(controller.apply, *endpoint(), updated)
                            policy.persist(rows)
                        except Exception:
                            try:
                                await asyncio.to_thread(controller.apply, *endpoint(), cfg)
                            except Exception:
                                stop.set()  # launchd reloads the last persisted policy.
                            raise
                        cfg.update(parts)
                        result = policy.snapshot()
            elif command == 'health':
                result = await asyncio.to_thread(metrics.snapshot, *endpoint())
            elif command == 'apply-node' and hasattr(args, 'update_key'):
                result = await apply_node(message['packet'])
            elif command == 'stop':
                stop.set()
                result = {'ok': True}
            elif command == 'verify':
                proxy = message.get('proxy')
                if not proxy:
                    proxy = f'http://127.0.0.1:{args.port}'
                # The local socket is 0600 and never exposes the unlocked profile.
                if not proxy.startswith(('http://127.0.0.1:', 'socks5h://127.0.0.1:')):
                    raise ValueError('Verification proxy must be loopback.')
                result = await asyncio.to_thread(verify.check, proxy, payload['expected_exit_ip'], 3)
            else:
                result = {'ok': False, 'error': 'Unknown local control command.'}
            writer.write(json.dumps(result).encode() + b'\n')
            await writer.drain()
        except Exception:
            with contextlib.suppress(Exception):
                writer.write(b'{"ok":false,"error":"Local control request failed."}\n')
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    try:
        ready_task = asyncio.create_task(ready.wait())
        done, _ = await asyncio.wait([ready_task, task], timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            task.result()
            raise RuntimeError('Native adapter exited during startup.')
        if ready_task not in done:
            ready_task.cancel()
            raise RuntimeError('Native adapter did not become ready.')
        if cfg is not None:
            await spawn()
            monitor = asyncio.create_task(watch())
        if policy is not None:
            uplink_monitor = asyncio.create_task(watch_direct_interface())
        if socket_path.exists():
            socket_path.unlink()
        listener = await asyncio.start_unix_server(connection, path=str(socket_path), limit=node_updates.LIMIT + 512)
        os.chmod(socket_path, 0o600)
        if ready_fd is not None:
            os.write(ready_fd, b'OK\n')
            os.close(ready_fd)
            ready_fd = None
        else:
            print('Native gateway ready. Ctrl-C stops it; host routing was not changed.', flush=True)
        stop_task = asyncio.create_task(stop.wait())
        watched = [t for t in (stop_task, task, monitor, uplink_monitor) if t is not None]
        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        for done_task in done:
            done_task.result()
    finally:
        stop.set()
        if ready_fd is not None:
            with contextlib.suppress(OSError):
                os.write(ready_fd, b'ERROR\n')
                os.close(ready_fd)
        if uplink_monitor:
            uplink_monitor.cancel()
            await asyncio.gather(uplink_monitor, return_exceptions=True)
        if listener:
            listener.close()
            await listener.wait_closed()
            socket_path.unlink(missing_ok=True)
        if child and child.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                child.terminate()
            try:
                await asyncio.wait_for(child.wait(), 5)
            except asyncio.TimeoutError:
                child.kill()
                await child.wait()
        if monitor:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        await asyncio.gather(task, return_exceptions=True)
        lock.close()


def start(payload, cfg, args, detach=False):
    if not detach:
        return asyncio.run(serve(payload, cfg, args))
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid:
        os.close(write_fd)
        import select
        ready, _, _ = select.select([read_fd], [], [], 25)
        result = os.read(read_fd, 100) if ready else b''
        os.close(read_fd)
        if result != b'OK\n':
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
            raise RuntimeError('Native background startup failed; check ports or use foreground mode.')
        print('Native background gateway ready. Use ./gateway status or ./gateway stop.')
        return
    os.close(read_fd)
    try:
        os.setsid()
        os.umask(0o077)
        fd = os.open(os.devnull, os.O_RDWR)
        for stream in (0, 1, 2):
            os.dup2(fd, stream)
        if fd > 2:
            os.close(fd)
        asyncio.run(serve(payload, cfg, args, write_fd))
        os._exit(0)
    except BaseException:
        with contextlib.suppress(OSError):
            os.write(write_fd, b'ERROR\n')
            os.close(write_fd)
        os._exit(1)
