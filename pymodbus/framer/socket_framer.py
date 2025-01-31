"""Socket framer."""
# pylint: disable=missing-type-doc
import struct

from pymodbus.exceptions import (
    ModbusIOException,
)
from pymodbus.framer.base import SOCKET_FRAME_HEADER, ModbusFramer
from pymodbus.logging import Log
from pymodbus.message.socket import MessageSocket


# --------------------------------------------------------------------------- #
# Modbus TCP Message
# --------------------------------------------------------------------------- #


class ModbusSocketFramer(ModbusFramer):
    """Modbus Socket Frame controller.

    Before each modbus TCP message is an MBAP header which is used as a
    message frame.  It allows us to easily separate messages as follows::

        [         MBAP Header         ] [ Function Code] [ Data ] \
        [ tid ][ pid ][ length ][ uid ]
          2b     2b     2b        1b           1b           Nb

        while len(message) > 0:
            tid, pid, length`, uid = struct.unpack(">HHHB", message)
            request = message[0:7 + length - 1`]
            message = [7 + length - 1:]

        * length = uid + function code + data
        * The -1 is to account for the uid byte
    """

    method = "socket"

    def __init__(self, decoder, client=None):
        """Initialize a new instance of the framer.

        :param decoder: The decoder factory implementation to use
        """
        super().__init__(decoder, client)
        self._hsize = 0x07
        self.message_handler = MessageSocket()

    def decode_data(self, data):
        """Decode data."""
        if len(data) > self._hsize:
            tid, pid, length, uid, fcode = struct.unpack(
                SOCKET_FRAME_HEADER, data[0 : self._hsize + 1]
            )
            return {
                "tid": tid,
                "pid": pid,
                "length": length,
                "slave": uid,
                "fcode": fcode,
            }
        return {}

    def frameProcessIncomingPacket(self, single, callback, slave, tid=None, **kwargs):
        """Process new packet pattern.

        This takes in a new request packet, adds it to the current
        packet stream, and performs framing on it. That is, checks
        for complete messages, and once found, will process all that
        exist.  This handles the case when we read N + 1 or 1 // N
        messages at a time instead of 1.

        The processed and decoded messages are pushed to the callback
        function to process and send.
        """
        while True:
            used_len, use_tid, dev_id, data = self.message_handler.decode(self._buffer)
            if not data:
                return
            self._header["uid"] = dev_id
            self._header["tid"] = use_tid
            self._header["pid"] = 0
            if not self._validate_slave_id(slave, single):
                Log.debug("Not a valid slave id - {}, ignoring!!", dev_id)
                self.resetFrame()
                return
            if (result := self.decoder.decode(data)) is None:
                self.resetFrame()
                raise ModbusIOException("Unable to decode request")
            self.populateResult(result)
            self._buffer = self._buffer[used_len:]
            self._header = {"tid": 0, "pid": 0, "len": 0, "uid": 0}
            if tid and tid != result.transaction_id:
                self.resetFrame()
            else:
                callback(result)  # defer or push to a thread?

    def buildPacket(self, message):
        """Create a ready to send modbus packet.

        :param message: The populated request/response to send
        """
        data = message.function_code.to_bytes(1, 'big') + message.encode()
        packet = self.message_handler.encode(data, message.slave_id, message.transaction_id)
        return packet
