#!/usr/bin/env python3
#
# Example 1-1 call signalling server
#
# Copyright (C) 2017 Centricular Ltd.
#
#  Author: Nirbheek Chauhan <nirbheek@centricular.com>
#

import os
import sys
import ssl
import logging
import asyncio
import websockets
import http
import multiprocessing

from concurrent.futures._base import TimeoutError

KEEPALIVE_TIMEOUT = 30 #config.keepalive_timeout

############### Global data ###############

# Format: {uid: (Peer WebSocketServerProtocol,
#                remote_address,
#                <'session'|room_id|None>)}
peers = dict()
# Format: {caller_uid: callee_uid,
#          callee_uid: caller_uid}
# Bidirectional mapping between the two peers
sessions = dict()
# Format: {room_id: {peer1_id, peer2_id, peer3_id, ...}}
# Room dict with a set of peers in each room
rooms = dict()

class MavWebRTCSignalServer(multiprocessing.Process):
    def __init__(self, config):
        multiprocessing.Process.__init__(self)
        self.daemon = True
        self.config = config
        self.logger = logging.getLogger('visiond.' + __name__)

        # Attempt to redirect the default handler into our log files
        default_server_logger = logging.getLogger('websockets.server')
        default_server_logger.setLevel(logging.DEBUG) # TODO: Set based on options 
        for handler in self.logger.handlers:
            default_server_logger.addHandler(handler)

        self.disable_ssl = False  # TODO: pass these in as options
        self.ADDR_PORT = ("0.0.0.0", 8443) # TODO: pass these in as options
        self.certpath = os.path.dirname(__file__) # TODO: pass these in as options
        self.health_check_path = "/health" # TODO: pass these in as options

        self.start() # the server will self start

    def run(self):
        # called by start()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        sslctx = None

        if not self.disable_ssl:
            # Create an SSL context to be used by the websocket server
            self.logger.info('Using TLS with keys in {!r}'.format(self.certpath))
            if 'letsencrypt' in self.certpath:
                chain_pem = os.path.join(self.certpath, 'fullchain.pem')
                key_pem = os.path.join(self.certpath, 'privkey.pem')
            else:
                chain_pem = os.path.join(self.certpath, 'cert.pem')
                key_pem = os.path.join(self.certpath, 'key.pem')

            sslctx = ssl.create_default_context()
            try:
                sslctx.load_cert_chain(chain_pem, keyfile=key_pem)
            except FileNotFoundError:
                self.logger.critical("Certificates not found, did you run generate_cert.sh?")
                sys.exit(1)
            # FIXME
            sslctx.check_hostname = False
            sslctx.verify_mode = ssl.CERT_NONE

        self.logger.info("Listening on https://{}:{}".format(*self.ADDR_PORT))
        # Websocket server
        start_server = websockets.serve(self.handler, *self.ADDR_PORT, ssl=sslctx, process_request=self.health_check,
                       # Maximum number of messages that websockets will pop
                       # off the asyncio and OS buffers per connection. See:
                       # https://websockets.readthedocs.io/en/stable/api.html#websockets.protocol.WebSocketCommonProtocol
                       max_queue=16)
        self.loop.run_until_complete(start_server)
        self.loop.run_forever()

    ############### Helper functions ###############

    async def health_check(self, path, request_headers):
        if path == self.health_check_path:
            return http.HTTPStatus.OK, [], b"OK\n"

    async def recv_msg_ping(self, ws, raddr):
        '''
        Wait for a message forever, and send a regular ping to prevent bad routers
        from closing the connection.
        '''
        msg = None
        while msg is None:
            try:
                msg = await asyncio.wait_for(ws.recv(), KEEPALIVE_TIMEOUT)
            except TimeoutError:
                self.logger.debug('Sending keepalive ping to {!r} in recv'.format(raddr))
                await ws.ping()
        return msg

    async def disconnect(self, ws, peer_id):
        '''
        Remove @peer_id from the list of sessions and close our connection to it.
        This informs the peer that the session and all calls have ended, and it
        must reconnect.
        '''
        global sessions
        if peer_id in sessions:
            del sessions[peer_id]
        # Close connection
        if ws and ws.open:
            # Don't care about errors
            asyncio.ensure_future(ws.close(reason='hangup'))

    async def cleanup_session(self, uid):
        if uid in sessions:
            other_id = sessions[uid]
            del sessions[uid]
            self.logger.debug("Cleaned up {} session".format(uid))
            if other_id in sessions:
                del sessions[other_id]
                self.logger.debug("Also cleaned up {} session".format(other_id))
                # If there was a session with this peer, also
                # close the connection to reset its state.
                if other_id in peers:
                    self.logger.debug("Closing connection to {}".format(other_id))
                    wso, oaddr, _ = peers[other_id]
                    del peers[other_id]
                    await wso.close()

    async def cleanup_room(self, uid, room_id):
        room_peers = rooms[room_id]
        if uid not in room_peers:
            return
        room_peers.remove(uid)
        for pid in room_peers:
            wsp, paddr, _ = peers[pid]
            msg = 'ROOM_PEER_LEFT {}'.format(uid)
            self.logger.debug('room {}: {} -> {}: {}'.format(room_id, uid, pid, msg))
            await wsp.send(msg)

    async def remove_peer(self, uid):
        await cleanup_session(uid)
        if uid in peers:
            ws, raddr, status = peers[uid]
            if status and status != 'session':
                await self.cleanup_room(uid, status)
            del peers[uid]
            await ws.close()
            self.logger.debug("Disconnected from peer {!r} at {!r}".format(uid, raddr))

    ############### Handler functions ###############

    async def connection_handler(self, ws, uid):
        global peers, sessions, rooms
        raddr = ws.remote_address
        peer_status = None
        peers[uid] = [ws, raddr, peer_status]
        self.logger.debug("Registered peer {!r} at {!r}".format(uid, raddr))
        while True:
            # Receive command, wait forever if necessary
            msg = await self.recv_msg_ping(ws, raddr)
            # Update current status
            peer_status = peers[uid][2]
            # We are in a session or a room, messages must be relayed
            if peer_status is not None:
                # We're in a session, route message to connected peer
                if peer_status == 'session':
                    other_id = sessions[uid]
                    wso, oaddr, status = peers[other_id]
                    assert(status == 'session')
                    self.logger.debug("{} -> {}: {}".format(uid, other_id, msg))
                    await wso.send(msg)
                # We're in a room, accept room-specific commands
                elif peer_status:
                    # ROOM_PEER_MSG peer_id MSG
                    if msg.startswith('ROOM_PEER_MSG'):
                        _, other_id, msg = msg.split(maxsplit=2)
                        if other_id not in peers:
                            await ws.send('ERROR peer {!r} not found'
                                        ''.format(other_id))
                            continue
                        wso, oaddr, status = peers[other_id]
                        if status != room_id:
                            await ws.send('ERROR peer {!r} is not in the room'
                                        ''.format(other_id))
                            continue
                        msg = 'ROOM_PEER_MSG {} {}'.format(uid, msg)
                        self.logger.debug('room {}: {} -> {}: {}'.format(room_id, uid, other_id, msg))
                        await wso.send(msg)
                    elif msg == 'ROOM_PEER_LIST':
                        room_id = peers[peer_id][2]
                        room_peers = ' '.join([pid for pid in rooms[room_id] if pid != peer_id])
                        msg = 'ROOM_PEER_LIST {}'.format(room_peers)
                        self.logger.debug('room {}: -> {}: {}'.format(room_id, uid, msg))
                        await ws.send(msg)
                    else:
                        await ws.send('ERROR invalid msg, already in room')
                        continue
                else:
                    raise AssertionError('Unknown peer status {!r}'.format(peer_status))
            # Requested a session with a specific peer
            elif msg.startswith('SESSION'):
                self.logger.debug("{!r} command {!r}".format(uid, msg))
                _, callee_id = msg.split(maxsplit=1)
                if callee_id not in peers:
                    await ws.send('ERROR peer {!r} not found'.format(callee_id))
                    continue
                if peer_status is not None:
                    await ws.send('ERROR peer {!r} busy'.format(callee_id))
                    continue
                await ws.send('SESSION_OK')
                wsc = peers[callee_id][0]
                self.logger.debug('Session from {!r} ({!r}) to {!r} ({!r})'
                    ''.format(uid, raddr, callee_id, wsc.remote_address))
                # Register session
                peers[uid][2] = peer_status = 'session'
                sessions[uid] = callee_id
                peers[callee_id][2] = 'session'
                sessions[callee_id] = uid
            # Requested joining or creation of a room
            elif msg.startswith('ROOM'):
                self.logger.debug('{!r} command {!r}'.format(uid, msg))
                _, room_id = msg.split(maxsplit=1)
                # Room name cannot be 'session', empty, or contain whitespace
                if room_id == 'session' or room_id.split() != [room_id]:
                    await ws.send('ERROR invalid room id {!r}'.format(room_id))
                    continue
                if room_id in rooms:
                    if uid in rooms[room_id]:
                        raise AssertionError('How did we accept a ROOM command '
                                            'despite already being in a room?')
                else:
                    # Create room if required
                    rooms[room_id] = set()
                room_peers = ' '.join([pid for pid in rooms[room_id]])
                await ws.send('ROOM_OK {}'.format(room_peers))
                # Enter room
                peers[uid][2] = peer_status = room_id
                rooms[room_id].add(uid)
                for pid in rooms[room_id]:
                    if pid == uid:
                        continue
                    wsp, paddr, _ = peers[pid]
                    msg = 'ROOM_PEER_JOINED {}'.format(uid)
                    self.logger.debug('room {}: {} -> {}: {}'.format(room_id, uid, pid, msg))
                    await wsp.send(msg)
            else:
                self.logger.debug('Ignoring unknown message {!r} from {!r}'.format(msg, uid))

    async def hello_peer(self, ws):
        '''
        Exchange hello, register peer
        '''
        raddr = ws.remote_address
        hello = await ws.recv()
        hello, uid = hello.split(maxsplit=1)
        if hello != 'HELLO':
            await ws.close(code=1002, reason='invalid protocol')
            raise Exception("Invalid hello from {!r}".format(raddr))
        if not uid or uid in peers or uid.split() != [uid]: # no whitespace
            await ws.close(code=1002, reason='invalid peer uid')
            raise Exception("Invalid uid {!r} from {!r}".format(uid, raddr))
        # Send back a HELLO
        await ws.send('HELLO')
        return uid

    async def handler(self, ws, path):
        '''
        All incoming messages are handled here. @path is unused.
        '''
        raddr = ws.remote_address
        self.logger.debug("Connected to {!r}".format(raddr))
        peer_id = await self.hello_peer(ws)
        try:
            await self.connection_handler(ws, peer_id)
        except websockets.ConnectionClosed:
            self.logger.debug("Connection to peer {!r} closed, exiting handler".format(raddr))
        finally:
            await self.remove_peer(peer_id)