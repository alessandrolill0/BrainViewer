"""OSC client to TouchDesigner. One-way: the backend sends, TD receives.

Two ports because an OSC In CHOP only handles numbers, and two operators cannot
share the same UDP port.
"""

import logging
import os

from pythonosc.udp_client import SimpleUDPClient

logger = logging.getLogger(__name__)

TD_OSC_HOST = os.getenv("TD_OSC_HOST", "127.0.0.1")
TD_OSC_PORT = int(os.getenv("TD_OSC_PORT", "9000"))
TD_OSC_EVENT_PORT = int(os.getenv("TD_OSC_EVENT_PORT", "9001"))


class TouchDesignerOSC:
    def __init__(self, host: str = TD_OSC_HOST, port: int = TD_OSC_PORT):
        self.host = host
        self.port = port
        self._client = SimpleUDPClient(host, port)
        logger.info("OSC client verso TouchDesigner: %s:%d", host, port)

    def send(self, address: str, *args) -> None:
        """Send an OSC message. UDP: never blocks, never fails if TD is down."""
        try:
            self._client.send_message(address, list(args) if args else [])
            logger.debug("OSC → %s %s", address, args)
        except OSError as exc:
            logger.warning("Invio OSC fallito su %s: %s", address, exc)

    def heartbeat(self, timestamp: float) -> None:
        self.send("/test/heartbeat", timestamp)


#: continuous number streams, read by an OSC In CHOP
osc = TouchDesignerOSC()

#: commands and events (text arguments allowed), read by an OSC In DAT
osc_events = TouchDesignerOSC(port=TD_OSC_EVENT_PORT)
