"""WebRTC signaling relay between TouchDesigner and the panel.

Before a video stream can open, the two peers must exchange a description
(offer/answer) and their addresses (candidates). The backend carries that
handshake; the video itself flows directly between them.

    signaling:  TD <--ws--> backend <--ws--> panel
    video:      TD <====== direct WebRTC ======> panel

The relay does not interpret messages, it forwards them as they are: SDP and
ICE semantics stay between the peers. It only adds presence notices, because
one side has to start and TD cannot guess when the panel opened.
"""

import logging

logger = logging.getLogger(__name__)

#: The only two roles. With several panels open the last one wins.
ROLES = ('td', 'panel')


class SignalingHub:
    """Keeps one connection per role and forwards messages to the other."""

    def __init__(self):
        self._peers: dict[str, object] = {}

    def peer_of(self, role: str) -> str:
        return 'panel' if role == 'td' else 'td'

    def connected(self, role: str) -> bool:
        return role in self._peers

    def state(self) -> dict:
        return {role: role in self._peers for role in ROLES}

    async def join(self, role: str, socket) -> None:
        """Register a connection and tell the other side someone is there."""
        previous = self._peers.get(role)
        if previous is not None and previous is not socket:
            logger.info('Segnalazione: %s riconnesso, la connessione precedente decade', role)
        self._peers[role] = socket
        logger.info('Segnalazione: %s connesso', role)
        await self._notify(self.peer_of(role), {'type': 'peer.joined', 'role': role})
        # The newcomer also needs to know whether the other side is already up,
        # otherwise it would wait forever.
        if self.connected(self.peer_of(role)):
            await self._send(socket, {'type': 'peer.joined', 'role': self.peer_of(role)})

    async def leave(self, role: str, socket) -> None:
        if self._peers.get(role) is not socket:
            return          # already replaced by a newer connection
        self._peers.pop(role, None)
        logger.info('Segnalazione: %s disconnesso', role)
        await self._notify(self.peer_of(role), {'type': 'peer.left', 'role': role})

    async def forward(self, role: str, message: dict) -> bool:
        """Forward to the other side. False if nobody is listening."""
        target = self.peer_of(role)
        if not self.connected(target):
            logger.debug('Segnalazione: messaggio da %s scartato, %s assente', role, target)
            return False
        await self._notify(target, {**message, 'from': role})
        return True

    async def _notify(self, role: str, message: dict) -> None:
        socket = self._peers.get(role)
        if socket is not None:
            await self._send(socket, message)

    async def _send(self, socket, message: dict) -> None:
        try:
            await socket.send_json(message)
        except Exception:
            # Socket died between the check and the send: must not bring down
            # the other side, which may still be negotiating.
            logger.debug('Segnalazione: invio fallito, socket scartato')
            for role, peer in list(self._peers.items()):
                if peer is socket:
                    self._peers.pop(role, None)
