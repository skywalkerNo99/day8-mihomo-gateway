import asyncio,contextlib,json,os,secrets,signal,struct,sys,tempfile,time,unittest
from pathlib import Path
from cryptography.exceptions import InvalidTag
from day8_gateway.adapter import Muon, StreamCipher, derive, address

class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def exchange(self, ack_only, size=16, wrong_salt=False, corrupt_payload=False):
        cfg={'server':'127.0.0.1','port':0,'cipher':f'aes-{size*8}-gcm','server_password':'synthetic-server','user_password':'synthetic-user','max_padding':32}
        user=derive('nebrix-muon-user-psk',cfg['user_password'].encode(),size)
        seen=asyncio.get_running_loop().create_future()
        payload=secrets.token_bytes(140000)
        async def serve(reader,writer):
            try:
                salt=await reader.readexactly(size)
                identity=await reader.readexactly(16)
                # Verify the complete request, including identity and split frames.
                from blake3 import blake3
                from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
                server=derive('nebrix-muon-server-psk',cfg['server_password'].encode(),size)
                mask=derive('muon 2026 identity subkey',server+salt,size)
                dec=Cipher(algorithms.AES(mask),modes.ECB()).decryptor()
                self.assertEqual(dec.update(identity)+dec.finalize(),blake3(user).digest()[:16])
                rx=StreamCipher(user,salt);header=rx.open(await reader.readexactly(27))
                self.assertEqual(header[0],0)
                variable=rx.open(await reader.readexactly(int.from_bytes(header[-2:],'big')+16))
                self.assertTrue(variable.startswith(address('example.com',443)))
                received=b''
                while len(received)<len(payload):
                    n=int.from_bytes(rx.open(await reader.readexactly(18)),'big')
                    received+=rx.open(await reader.readexactly(n+16))
                self.assertEqual(received,payload)
                response_salt=secrets.token_bytes(size)
                tx=StreamCipher(user,response_salt)
                reply=b'response-body'
                fixed=b'\x01'+struct.pack('!Q',int(time.time()))+(b'!'*size if wrong_salt else salt)+struct.pack('!H',0 if ack_only else len(reply))
                writer.write(response_salt+tx.seal(fixed))
                if ack_only:writer.write(tx.seal(struct.pack('!H',len(reply))))
                data=tx.seal(reply)
                if corrupt_payload:data=data[:-1]+bytes([data[-1]^1])
                # Deliberately fragment the encrypted payload across TCP writes.
                writer.write(data[:3]);await writer.drain();await asyncio.sleep(.01)
                writer.write(data[3:]);await writer.drain()
                seen.set_result(True)
            except Exception as e:
                if not seen.done():seen.set_exception(e)
            finally:
                writer.close();await writer.wait_closed()
        server=await asyncio.start_server(serve,'127.0.0.1',0)
        cfg['port']=server.sockets[0].getsockname()[1]
        stream=await Muon(cfg).connect('example.com',443)
        try:
            await stream.send(payload)
            if wrong_salt:
                with self.assertRaisesRegex(ValueError,'salt mismatch'):await asyncio.wait_for(stream.receive(),3)
            elif corrupt_payload:
                with self.assertRaises(InvalidTag):await asyncio.wait_for(stream.receive(),3)
            else:self.assertEqual(await asyncio.wait_for(stream.receive(),3),b'response-body')
            await asyncio.wait_for(seen,3)
        finally:
            await stream.close();server.close();await server.wait_closed()

    async def test_empty_ack_and_fragmented_response(self):await self.exchange(True)
    async def test_payload_in_initial_header(self):await self.exchange(False)
    async def test_aes256_wire_format(self):await self.exchange(True,32)
    async def test_wrong_request_salt_rejected(self):await self.exchange(True,wrong_salt=True)
    async def test_corrupt_tag_rejected(self):await self.exchange(True,corrupt_payload=True)

class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def exits_with_open_client(self, sig):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json'
            config.write_text(json.dumps({'server':'127.0.0.1','port':9,'cipher':'aes-128-gcm','server_password':'synthetic-server','user_password':'synthetic-user'}))
            env={**os.environ,'MUON_CONFIG':str(config)}
            p=await asyncio.create_subprocess_exec(sys.executable,'-m','day8_gateway.adapter',env=env,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
            writer=None
            try:
                for _ in range(100):
                    try:
                        reader,writer=await asyncio.open_connection('127.0.0.1',1080)
                        break
                    except ConnectionRefusedError:await asyncio.sleep(.02)
                self.assertIsNotNone(writer,'adapter never listened')
                # Leave a client blocked halfway through SOCKS negotiation.
                writer.write(b'\x05\x02\x00');await writer.drain()
                p.send_signal(sig)
                await asyncio.wait_for(p.wait(),3)
                self.assertEqual(p.returncode,0)
                self.assertEqual(await asyncio.wait_for(reader.read(),1),b'')
            finally:
                if writer:
                    writer.close()
                    with contextlib.suppress(Exception):await writer.wait_closed()
                if p.returncode is None:
                    p.kill();await p.wait()

    async def test_sigint_exits_with_open_client(self):await self.exits_with_open_client(signal.SIGINT)
    async def test_sigterm_exits_with_open_client(self):await self.exits_with_open_client(signal.SIGTERM)

if __name__=='__main__':unittest.main(verbosity=2)
